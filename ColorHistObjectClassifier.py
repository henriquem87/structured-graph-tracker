###############################################################################
#
# Copyright (c) 2016, Henrique Morimitsu,
# University of Sao Paulo, Sao Paulo, Brazil
#
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
# 3. Neither the name of the copyright holder nor the names of its contributors
#    may be used to endorse or promote products derived from this software
#    without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# #############################################################################

import cv2
import math
import numpy as np
import Utils
from ObjectClassifier import ObjectClassifier


class ColorHistObjectClassifier(ObjectClassifier):
    def __init__(self, hsv_img, bb, hist_channels, hist_mask, hist_num_bins,
                 hist_intervals):
        """ Compute the model histogram from the initial bounding box.
        hsv_img is a HSV colored image matrix
        bb is the bounding box from where the histogram will be computed
        hist_channels is a list of the indices of the channels that will be used
        hist_mask is a binary image that represents the are that should be
            considered from the image
        hist_num_bins is a list of how many bins each channels will have
        hist_intervals is a list of lower and upper bounds for the values in
            each channel

        For more information about the last 4 parameters, consult the OpenCV
        documentation about the calcHist function.
        """
        self._bb = bb
        self._obj_img = hsv_img[int(bb.top()):int(bb.bottom()),
                                int(bb.left()):int(bb.right())]
        self._color_hist_params = [hist_channels, hist_mask, hist_num_bins,
                                   hist_intervals]
        self._color_hist = self.compute_object_histogram(
            hsv_img, bb, *self._color_hist_params)

    def compute_object_histogram(self, img, objectBB, channels, mask, num_bins,
                                 intervals):
        """ Computes the color histogram of a bounding box.
        The color model corresponds to the method proposed in:
        Patrick Perez, Carine Hue, Jaco Vermaak and Michel Gangnet.
        Color-based probabilistic tracking. In European Conference on Computer
        Vision, pages 661-675. Springer.
        """
        obj_hist = np.zeros((num_bins[0] * num_bins[1] + num_bins[2]),
                            np.float32)
        obj_image = img[int(objectBB.top()):int(objectBB.bottom()),
                        int(objectBB.left()):int(objectBB.right())]

        # Creates a separated image for each channel
        splitted_img = cv2.split(obj_image)
        if len(splitted_img) == 3:
            maskH = cv2.threshold(splitted_img[0], int(0.1 * 255), 255,
                                  cv2.THRESH_BINARY)[1]
            maskS = cv2.threshold(splitted_img[1], int(0.2 * 255), 255,
                                  cv2.THRESH_BINARY)[1]
            maskHS = cv2.bitwise_and(maskH, maskS)
            white_mask = np.ones_like(maskH) * 255
            maskV = cv2.bitwise_xor(maskHS, white_mask)
            hs_hist = cv2.calcHist([obj_image], channels[:2], maskHS,
                                   num_bins[:2], intervals[:4])
            v_hist = cv2.calcHist([obj_image], channels[2:3], maskV,
                                  num_bins[2:3], intervals[4:])
            obj_hist = np.concatenate((hs_hist.flatten(), v_hist.flatten()))
            obj_hist /= np.sum(obj_hist)

        return obj_hist

    def particle_weight(self, particles, hsv_img, mask=None):
        """ Computes the new weight of a particle. This function computes the
        likelihood P(z|x), where z is the observation (color histogram) and x
        the state. This implementations corresponds to the function proposed in:
        Erkut Erdem, Severine Dubuisson and Isabelle Bloch.
        Fragments based tracking with adaptive cue integration. Computer Vision
        and Image Understanding, 116 (7):827-841.
        """
        all_weights = np.empty((particles.shape[0], 1), np.float64)
        num_bins = self._color_hist_params[2]
        all_particle_hists = np.empty(
            (particles.shape[0],num_bins[0]*num_bins[1]+num_bins[2]),
            np.float64)
        for i, state in enumerate(particles):
            particleBB = self._bb.centered_on(state[0], state[1])
            particle_hist = self.compute_object_histogram(
                hsv_img, particleBB, self._color_hist_params[0], mask,
                self._color_hist_params[2], self._color_hist_params[3])
            all_particle_hists[i] = particle_hist
            
        norm_color_hist = self._color_hist / np.sum(self._color_hist)
        norm_color_hist = norm_color_hist[np.newaxis]
        sum_hist = np.sum(all_particle_hists, axis=1, keepdims=True)
        sum_hist[sum_hist == 0] = 1.0
        norm_particle_hist = all_particle_hists / sum_hist
        dist = (1.0/np.sqrt(2)) * \
               np.sqrt(np.sum(
                   np.power(np.sqrt(norm_color_hist) - \
                            np.sqrt(norm_particle_hist), 2), axis=1))

        sigma = 0.1
        all_weights = np.exp(-(np.power(dist, 2)) / (2 * np.power(sigma, 2)))

        positive_x = particles[:, 0] >= 0
        small_x = particles[:, 0] < hsv_img.shape[1]
        positive_y = particles[:, 1] >= 0
        small_y = particles[:, 1] < hsv_img.shape[0]
        valid_mask = positive_x * small_x * positive_y * small_y
        
        all_weights = all_weights * valid_mask
        all_weights = all_weights[:, np.newaxis]
        
        return all_weights

    def score_object(self, hsv_img, bb, mask=None):
        """ Compute the score of an object represented by bb, according to
        this classifier parameters.
        """
        return self.particle_weight(bb.centroid(), hsv_img, mask)

    def update_object_histogram(self, new_histogram, update_factor=0.1):
        """ Compute a linear combination of the current model
        histogram with another one.
        """
        self._color_hist = (1.0 - update_factor) * self._color_hist + \
            update_factor * new_histogram

    @property
    def color_hist_params(self):
        return self._color_hist_params
