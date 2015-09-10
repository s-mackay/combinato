#!/usr/bin/env python
# -*- coding: utf-8 -*-
# JN 2015-01-04
"""
script creates an artifact column in data_ h5 files
different functions can be called to mark artifacts
"""


from __future__ import print_function, division, absolute_import
import os
from argparse import ArgumentParser

import numpy as np
import tables

from .. import h5files

SIGNS = ('pos', 'neg')
DEBUG = True
RESET = True  # set artifacts to 0 before analysis


options_by_diff = {'art_id': 1,   # to identify this type of artifact
                   'name': 'high_firing',
                   'binlength': 500,  # msec
                   'max_spk_per_bin': 100}  # means 200 Hz maximum

options_by_height = {'art_id': 2,
                     'name': 'amplitude',
                     'max_height': 1000}  # µV

options_by_bincount = {'art_id': 4,
                       'name': 'bincount',
                       'max_frac_ch': .5}

options_double = {'art_id': 8,
                  'name': 'double',
                  'relevant_idx': 18,  # look here for decision, very specific!
                  'min_dist': 1.5}  # means 1.5 ms minimum distance

artifact_types = (options_by_diff, options_by_height,
                  options_by_bincount, options_double)

id_to_name = {}

for options in artifact_types:
    id_to_name[options['art_id']] = options['name']
    

def mark_double_detection(times, spikes, sign):
    """
    for spikes that are too close together,
    keep only the one with the bigger amplitude
    """

    def mycmp(a, b, sign):
        if sign == 'pos':
            return a > b
        elif sign == 'neg':
            return a < b

    artifacts = np.zeros(times.shape[0], dtype=bool)
    min_dist = options_double['min_dist']
    rel_idx = options_double['relevant_idx']
    diff = np.diff(times)
    double_idx = diff < min_dist

    for i in double_idx.nonzero()[0]:
        sp1 = spikes[i, rel_idx]
        sp2 = spikes[i + 1, rel_idx]
        if mycmp(sp1, sp2, sign):
            kill = i + 1
        else:
            kill = i
        artifacts[kill] = True

    print('{} dist < {}'.format((double_idx).sum(), min_dist))
    return artifacts, options_double['art_id']


def mark_by_diff(times):
    """
    marks bins with too many events
    """
    bin_len = options_by_diff['binlength']
    max_per_bin = options_by_diff['max_spk_per_bin']

    artifacts = np.zeros(times.shape[0], dtype=bool)

    for shift in (0, bin_len/2):
        bins = np.arange(times[0] + shift, times[-1] + shift, bin_len)
        if len(bins) < 2:
            continue
        counts, _ = np.histogram(times, bins)
        left_edges_too_many = bins[:-1][counts > max_per_bin]
        # try a loop, but maybe too slow?
        if DEBUG:
            print('looping over {} edges'.format(left_edges_too_many.shape[0]))
        for edge in left_edges_too_many:
            idx = (times >= edge) & (times <= edge + bin_len)
            artifacts[idx] = True

    return artifacts, options_by_diff['art_id']


def bincount_to_edges(concurrent_fname):
    """
    helper, transforms bincount to edges
    """
    conc_fid = tables.open_file(concurrent_fname)
    count = conc_fid.root.count[:]
    attrs = conc_fid.root.count.attrs
    num_channels = attrs['nch']
    start = attrs['start']
    stop = attrs['stop']
    bin_len = attrs['binms']
    conc_fid.close()
    bins = np.arange(start, stop, bin_len)
    cutoff = options_by_bincount['max_frac_ch'] * num_channels
    if DEBUG:
        print('Using cutoff of {:.0f} channels'.format(cutoff))
    exclusion_left_edges = bins[:-1][count > cutoff]
    return exclusion_left_edges, bin_len


def mark_by_bincount(times, left_edges, bin_len):
    """
    marks bins with events in too many other channels (specified by counts)
    """
    if DEBUG:
        print('all channel rejection, looping over {} edges'.\
               format(left_edges.shape[0]))

    artifacts = np.zeros(times.shape[0], dtype=bool)

    for edge in left_edges:
        idx = (times >= edge) & (times <= edge + bin_len)
        artifacts[idx] = True

    return artifacts, options_by_bincount['art_id']


def mark_by_height(spikes, sign):
    """
    marks spikes that exceed a height criterion
    """
    max_height = options_by_height['max_height']

    if sign == 'pos':
        artifacts = spikes.max(1) >= max_height
    elif sign == 'neg':
        artifacts = spikes.min(1) <= -max_height
    else:
        raise ValueError('Unknown sign: ' + sign)

    return artifacts, options_by_height['art_id']


def main(fname, concurrent_edges=None, concurrent_bin=None):
    """
    creates table to store artifact information
    """
    for sign in SIGNS:

        h5fid = tables.open_file(fname, 'r+')

        try:
            node = h5fid.get_node('/' + sign + '/times')
        except tables.NoSuchNodeError:
            print('{} has no {} spikes'.format(fname, sign))
            continue

        if len(node.shape) == 0:
            continue

        elif node.shape[0] == 0:
            continue

        times = node[:]
        num_spk = times.shape[0]

        spikes = h5fid.get_node('/' + sign, 'spikes')[:, :]

        assert num_spk == spikes.shape[0]

        try:
            artifacts = h5fid.get_node('/' + sign + '/artifacts')
        except tables.NoSuchNodeError:
            h5fid.create_array('/' + sign, 'artifacts',
                               atom=tables.Int8Atom(), shape=(num_spk, ))
            artifacts = h5fid.get_node('/' + sign + '/artifacts')

        if RESET:
            artifacts[:] = 0

        arti_by_diff, arti_by_diff_id = mark_by_diff(times)
        artifacts[arti_by_diff != 0] = arti_by_diff_id
        if DEBUG:
            print('Marked {} {} spikes by diff'.
                  format(arti_by_diff.sum(), sign))

        arti_by_height, arti_by_height_id = mark_by_height(spikes, sign)
        artifacts[arti_by_height != 0] = arti_by_height_id
        if DEBUG:
            print('Marked {} {} spikes by height'.
                  format(arti_by_height.sum(), sign))

        if concurrent_edges is not None:
            arti_by_conc, arti_by_conc_id = mark_by_bincount(times,
                                                             concurrent_edges,
                                                             concurrent_bin)

            artifacts[arti_by_conc != 0] = arti_by_conc_id
            if DEBUG:
                print('Marked {} {} spikes by concurrent occurence'.
                      format(arti_by_conc.sum(), sign))

        arti_by_double, double_id = mark_double_detection(times, spikes, sign)
        artifacts[arti_by_double != 0] = double_id

        h5fid.close()


def parse_args():
    CONC_FNAME = 'concurrent_times.h5'
    parser = ArgumentParser()
    parser.add_argument('--file', nargs=1)
    parser.add_argument('--no-concurrent', action='store_true',
                        default=False)
    parser.add_argument('--concurrent-file', nargs=1)
    args = parser.parse_args()

    if not args.no_concurrent:
        if args.concurrent_file:
            conc_fname = args.concurrent_file[0]
        else:
            conc_fname = CONC_FNAME
        concurrent_edges, concurrent_bin =\
            bincount_to_edges(conc_fname)
    else:
        concurrent_edges = concurrent_bin = None

    if args.file:
        fname = args.file[0]
    else:
        fname = os.getcwd()

    if os.path.isdir(fname):
        files = h5files(fname)
    else:
        files = [fname]

    # main loop, could be done with parallel
    # processing (bad because of high I/O)
    for fname in files:
        if DEBUG:
            print('Starting ' + fname)
        main(fname, concurrent_edges, concurrent_bin)

if __name__ == "__main__":
    parse_args()
