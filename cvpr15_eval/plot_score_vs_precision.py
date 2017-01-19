#!/usr/bin/env python

from __future__ import print_function
from __future__ import unicode_literals
from builtins import zip
from builtins import range
import os.path
import sys
sys.path.append('.')
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '/../hytra/.')

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import argparse
import math
import structsvm
import trackingfeatures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Scatter-Plot the precision of a lineage vs the reranker score')

    # file paths
    parser.add_argument('--lineage', type=str, required=True, dest='lineage_filename',
                        help='Lineage tree dump')
    parser.add_argument('--precisions', type=str, required=True, dest='precisions_filename',
                        help='file containing the precision against the ground truth of each lineage tree')
    parser.add_argument('--reranker-weights', type=str, dest='reranker_weights',
                        help='file containing the learned reranker weights')
    parser.add_argument('-o', required=True, type=str, dest='out_file',
                        help='Name of the file the plot is saved to')
    parser.add_argument('--min-length', type=int, default=1, dest='min_length',
                        help='Minimal length that a lineage tree must have to be included in the plots')

    options = parser.parse_args()

    # load
    precisions = np.loadtxt(options.precisions_filename)
    tracks, divisions, lineage_trees = trackingfeatures.load_lineage_dump(options.lineage_filename)
    print("Found {} tracks, {} divisions and {} lineage trees".format(len(tracks),
                                                                      len(divisions),
                                                                      len(lineage_trees)))
    weights = np.loadtxt(options.reranker_weights)
    means = np.loadtxt(os.path.splitext(options.reranker_weights)[0] + '_means.txt')
    variances = np.loadtxt(os.path.splitext(options.reranker_weights)[0] + '_variances.txt')

    # compute scores
    scores = []
    num_divs = []
    num_tracks = []
    lengths = []
    marker_sizes = []
    valid_indices = []
    for i, lt in enumerate(lineage_trees):
        length = sum([t.length for t in lt.tracks])
        if length < options.min_length:
            continue
        valid_indices.append(i)
        lengths.append(length)
        # marker_sizes.append(np.log(length))
        marker_sizes.append(length)
        feat_vec = np.expand_dims(lt.get_expanded_feature_vector([-1, 2]), axis=1)
        structsvm.utils.apply_feature_normalization(feat_vec, means, variances)
        score = np.dot(weights, feat_vec[:, 0])
        scores.append(score)
        num_divs.append(len(lt.divisions))
        num_tracks.append(len(lt.tracks))

    filename, extension = os.path.splitext(options.out_file)
    precisions = precisions[valid_indices]

    # scatter plot
    plt.figure()
    plt.hold(True)
    plt.scatter(precisions, scores, s=marker_sizes, alpha=0.5)
    plt.xlabel("Precision")
    plt.ylabel("Score")
    plt.savefig(options.out_file)

    # length histogram
    plt.figure()
    plt.hist(lengths, 100)
    plt.xlabel("Length")
    plt.ylabel("Frequency")
    plt.savefig(filename + "_length_histo" + extension)

    # sort according to precision and plot again
    # log_scores = map(math.log, scores)
    prec_score_pairs = list(zip(list(precisions), scores, num_divs, num_tracks, lengths))
    prec_score_pairs.sort(key=lambda x: x[1]) # sort by score

    plt.figure()
    plt.plot(list(range(len(prec_score_pairs))), zip(*prec_score_pairs)[0])
    plt.ylabel("Precision")
    plt.xlabel("Num Tracks, sorted by score")
    plt.savefig(filename + "_sorted_num_tracks" + extension)

    plt.figure()
    plt.hold(True)
    plt.plot(zip(*prec_score_pairs)[1], zip(*prec_score_pairs)[0])
    plt.ylabel("Precision")
    plt.xlabel("Score")
    plt.savefig(filename + "_sorted" + extension)

    plt.figure()
    plt.hold(True)
    plt.scatter(zip(*prec_score_pairs)[2], zip(*prec_score_pairs)[1], c='b', label='Num divisions')
    plt.scatter(zip(*prec_score_pairs)[3], zip(*prec_score_pairs)[1], c='r', label='Num tracks')
    # plt.plot(zip(*prec_score_pairs)[4], zip(*prec_score_pairs)[1], c='g', label='overall lengths')
    plt.xlabel("Length")
    plt.ylabel("Score")
    plt.legend()
    plt.savefig(filename + "_length_score" + extension)

    plt.figure()
    plt.hold(True)
    plt.scatter(zip(*prec_score_pairs)[2], zip(*prec_score_pairs)[0], c='b', label='Num divisions')
    plt.scatter(zip(*prec_score_pairs)[3], zip(*prec_score_pairs)[0], c='r', label='Num tracks')
    # plt.scatter(zip(*prec_score_pairs)[4], zip(*prec_score_pairs)[0], c='g', label='overall lengths')
    plt.xlabel("Length")
    plt.ylabel("Precision")
    plt.legend()
    plt.savefig(filename + "_length_precision" + extension)

    # plot only outlier svm score (averaged over lineage) vs precision
    outlier_svm_scores = []
    track_outlier_feature_idx = trackingfeatures.LineagePart.feature_to_weight_idx('track_outlier_svm_score')
    div_outlier_feature_idx = trackingfeatures.LineagePart.feature_to_weight_idx('div_outlier_svm_score')
    for i, lt in enumerate(lineage_trees):
        if i not in valid_indices:
            continue
        fv = lt.get_feature_vector()
        outlier_svm_scores.append(fv[track_outlier_feature_idx] + fv[div_outlier_feature_idx])

    plt.figure()
    plt.hold(True)
    plt.scatter(precisions, outlier_svm_scores, s=marker_sizes, alpha=0.5)
    plt.xlabel("Precision")
    plt.ylabel("Outlier SVM Score")
    plt.savefig(filename + "_outlier_score" + extension)
