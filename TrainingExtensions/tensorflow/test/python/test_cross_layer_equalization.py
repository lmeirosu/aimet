# /usr/bin/env python3.5
# -*- mode: python -*-
# =============================================================================
#  @@-COPYRIGHT-START-@@
#
#  Copyright (c) 2019, Qualcomm Innovation Center, Inc. All rights reserved.
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#
#  1. Redistributions of source code must retain the above copyright notice,
#     this list of conditions and the following disclaimer.
#
#  2. Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions and the following disclaimer in the documentation
#     and/or other materials provided with the distribution.
#
#  3. Neither the name of the copyright holder nor the names of its contributors
#     may be used to endorse or promote products derived from this software
#     without specific prior written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
#  AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
#  IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
#  ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
#  LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
#  CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
#  SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
#  INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
#  CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
#  ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
#  POSSIBILITY OF SUCH DAMAGE.
#
#  SPDX-License-Identifier: BSD-3-Clause
#
#  @@-COPYRIGHT-END-@@
# =============================================================================
""" This file contains unit tests for testing cross layer scaling feature of CLE """


import unittest
import numpy as np
import tensorflow as tf

import aimet_tensorflow.utils.graph_saver
from aimet_tensorflow.cross_layer_equalization import CrossLayerScaling, GraphSearchUtils, equalize_model
from aimet_tensorflow.utils.op.conv import WeightTensorUtils, BiasUtils
from aimet_tensorflow.common import graph_eval


class TestCrossLayerEqualization(unittest.TestCase):
    """ Test methods for Cross layer equalization """

    @staticmethod
    def _custom_two_conv_layer_model():
        """
        Builds a custom model with two conv layers
        :return:
        """
        tf.reset_default_graph()
        inputs = tf.keras.Input(shape=(32, 32, 3,), name="inputs")
        conv1_op = tf.keras.layers.Conv2D(32, (3, 3))(inputs)
        conv2_op = tf.keras.layers.Conv2D(32, (3, 3))(conv1_op)
        model = tf.nn.relu(conv2_op)

        return model

    def test_find_layer_groups_to_scale_custom_model_with_candidate_layers(self):
        """ Test find_layer_groups_to_scale() on a custom model """

        _ = TestCrossLayerEqualization._custom_two_conv_layer_model()

        init = tf.global_variables_initializer()
        sess = tf.Session()
        sess.run(init)
        start_op = "inputs"

        graph_util = GraphSearchUtils(tf.get_default_graph(), start_op, 'Relu')
        layer_groups = graph_util.find_layer_groups_to_scale()
        self.assertEqual(1, len(layer_groups))

    def test_find_layers_groups_tp_scale_custom_model_without_candidate_layers(self):
        """ Test find_layer_groups_to_scale() on a model without potential layers for scaling """

        tf.reset_default_graph()
        inputs = tf.keras.Input(shape=(32, 32, 3,), name="inputs")
        conv_op = tf.keras.layers.Conv2D(32, (3, 3))(inputs)
        bn_op = tf.keras.layers.BatchNormalization(fused=True)(conv_op)
        _ = tf.nn.relu(bn_op)

        init = tf.global_variables_initializer()
        sess = tf.Session()
        sess.run(init)

        graph_util = GraphSearchUtils(tf.get_default_graph(), "inputs", 'Relu')
        layer_groups = graph_util.find_layer_groups_to_scale()
        self.assertEqual(0, len(layer_groups))

    def test_update_weight_tensor_for_op(self):
        """ Test update_weight_tensor_for_op() on custom conv op """

        # get VGG16 model
        tf.reset_default_graph()
        inputs = tf.keras.Input(shape=(32, 32, 3,), name="inputs")
        conv_op = tf.keras.layers.Conv2D(32, (3, 3))(inputs)
        _ = tf.nn.relu(conv_op)

        init = tf.global_variables_initializer()
        sess = tf.Session()
        sess.run(init)

        conv_op = sess.graph.get_operation_by_name('conv2d/Conv2D')

        initial_data = WeightTensorUtils.get_tensor_as_numpy_data(sess, conv_op)

        wt_data = initial_data + 2

        # this is block1_conv1/Conv2D in VGG16
        WeightTensorUtils.update_tensor_for_op(sess, conv_op, wt_data)
        new_sess = aimet_tensorflow.utils.graph_saver.save_and_load_graph('./temp_conv_wt_updated', sess)

        # check for if reroute was successful
        # read op from conv op should be same as one defined by new variable type
        conv_op = new_sess.graph.get_operation_by_name('conv2d/Conv2D')
        new_wt_data = WeightTensorUtils.get_tensor_as_numpy_data(new_sess, conv_op)

        assert not np.allclose(initial_data, new_wt_data)

    def test_scale_cls_set_with_conv_layers_custom_model(self):
        """
        Test scale_cls_set_with_conv_layers() on a custom model
        """

        _ = TestCrossLayerEqualization._custom_two_conv_layer_model()

        init = tf.global_variables_initializer()
        sess = tf.Session()
        sess.run(init)

        graph_util = GraphSearchUtils(tf.get_default_graph(), "inputs", 'Relu')
        layer_groups_as_tf_ops = graph_util.find_layer_groups_to_scale()
        scaling_factors = CrossLayerScaling.scale_cls_set_with_conv_layers(sess, layer_groups_as_tf_ops[0])
        self.assertEqual(32, len(scaling_factors))

        range_conv1_after_scaling = np.amax(np.abs(WeightTensorUtils.get_tensor_as_numpy_data(
            sess, layer_groups_as_tf_ops[0][0])), axis=(2, 0, 1))
        range_conv2_after_scaling = np.amax(np.abs(WeightTensorUtils.get_tensor_as_numpy_data(
            sess, layer_groups_as_tf_ops[0][1])), axis=(3, 0, 1))

        assert np.allclose(range_conv1_after_scaling, range_conv2_after_scaling)

    def test_scale_cls_set_with_depthwise_conv_layer_custom_model(self):
        """
        Test test_scale_cls_set_with_depthwise_layers() on a custom model
        """

        tf.reset_default_graph()
        inputs = tf.keras.Input(shape=(10, 10, 3,))
        x = tf.keras.layers.Conv2D(10, (1, 1))(inputs)
        y = tf.keras.layers.DepthwiseConv2D((3, 3), padding='valid',depth_multiplier=1, strides=(1,1), use_bias=False)(x)
        z = tf.keras.layers.Conv2D(10, (1, 1))(y)
        _ = tf.nn.relu(z)

        init = tf.global_variables_initializer()
        sess = tf.Session(graph = tf.get_default_graph())
        sess.run(init)

        graph_util = GraphSearchUtils(tf.get_default_graph(), "input_1", 'Relu')
        layer_groups_as_tf_ops = graph_util.find_layer_groups_to_scale()
        scaling_matrix12, scaling_matrix23 = CrossLayerScaling.scale_cls_set_with_depthwise_layers(
            sess, layer_groups_as_tf_ops[0])
        self.assertEqual(10, len(scaling_matrix12))
        self.assertEqual(10, len(scaling_matrix23))

    def test_scale_model_custom(self):
        """ Test scale_model on a custom model """

        _ = TestCrossLayerEqualization._custom_two_conv_layer_model()
        init = tf.global_variables_initializer()
        sess = tf.Session()
        sess.run(init)
        new_sess, scaling_factors = CrossLayerScaling.scale_model(sess, "inputs", 'Relu')
        # scaling factors for number of groups selected for scaling returned
        self.assertEqual(1, len(scaling_factors))

    def test_relu6_replaced_with_relu(self):
        """
        Test replacing Relu6 wth Relu
        """
        tf.reset_default_graph()
        inputs = tf.keras.Input(shape=(32, 32, 3,))
        conv_op = tf.keras.layers.Conv2D(32, (3, 3))(inputs)
        _ = tf.nn.relu6(conv_op)

        init = tf.global_variables_initializer()
        sess = tf.Session()
        sess.run(init)

        bias_add = sess.graph.get_operation_by_name('conv2d/BiasAdd')
        self.assertEqual('Relu6', bias_add.outputs[0].consumers()[0].type)

        #update Relu
        start_op = "input_1"
        graph_util = GraphSearchUtils(sess.graph, start_op, 'Relu6')
        after_relu_replace_sess = graph_util.find_and_replace_relu6_with_relu(sess)

        updated_bias_add = after_relu_replace_sess.graph.get_operation_by_name('conv2d/BiasAdd')
        self.assertEqual('Relu', updated_bias_add.outputs[0].consumers()[0].type)


    def test_high_bias_fold_with_custom_model(self):
        """
        Test high bias fold with a custom model
        """
        tf.reset_default_graph()
        inputs = tf.keras.Input(shape=(32, 32, 3,))

        conv_op = tf.keras.layers.Conv2D(32, (3, 3))(inputs)
        bn_op = tf.keras.layers.BatchNormalization(fused=True)(conv_op)
        relu_1= tf.nn.relu(bn_op)

        conv2_op = tf.keras.layers.Conv2D(32, (3, 3))(relu_1)
        bn_op_2 = tf.keras.layers.BatchNormalization(fused=True)(conv2_op)
        relu_2 = tf.nn.relu(bn_op_2)

        init = tf.global_variables_initializer()
        sess = tf.Session()
        sess.run(init)
        np.random.seed(0)

        old_conv_op = sess.graph.get_operation_by_name('conv2d/Conv2D')

        b_shape = BiasUtils.get_shape(old_conv_op)
        numpy_data = np.random.rand(b_shape[0])
        BiasUtils.update_bias_for_op(sess, old_conv_op, numpy_data)
        graph_eval.initialize_uninitialized_vars(sess)

        # save and load the updated graph after high bias fold update
        n_sess = aimet_tensorflow.utils.graph_saver.save_and_load_graph('./test_update', sess)

        conv_op = n_sess.graph.get_operation_by_name('conv2d/Conv2D')
        bias_data= BiasUtils.get_bias_as_numpy_data(n_sess, conv_op)

        new_sess = equalize_model(n_sess, conv_op.inputs[0].op.name, 'Relu_1')

        new_conv_op = new_sess.graph.get_operation_by_name('conv2d/Conv2D')
        bias_data_after_fold = BiasUtils.get_bias_as_numpy_data(new_sess, new_conv_op)

        for i in range(len(bias_data_after_fold)):
            self.assertTrue(bias_data_after_fold[i] <= bias_data[i])

    def test_bias_add_custom_model(self):
        """ test update bias when no bias present """

        tf.reset_default_graph()
        inputs = tf.keras.Input(shape=(32, 32, 3,))

        conv_op = tf.keras.layers.Conv2D(32, (3, 3), use_bias=False)(inputs)

        conv2_op = tf.keras.layers.Conv2D(32, (3, 3), use_bias=False)(inputs)
        relu2= tf.nn.relu(conv2_op)

        add = tf.keras.layers.add([conv_op, relu2])
        relu= tf.nn.relu(add)

        init = tf.global_variables_initializer()
        sess = tf.Session()
        sess.run(init)

        shape = WeightTensorUtils.get_tensor_shape(conv_op.op)
        np.random.seed(0)
        bias_data = np.random.rand(shape[3])

        assert BiasUtils.is_bias_none(conv_op.op)
        BiasUtils.update_bias_for_op(sess, conv_op.op, bias_data)
        n_sess = aimet_tensorflow.utils.graph_saver.save_and_load_graph('./test_update', sess)

        conv_op_updated = n_sess.graph.get_operation_by_name(conv_op.op.name)
        assert not BiasUtils.is_bias_none(conv_op_updated)
        updated_bias = BiasUtils.get_bias_as_numpy_data(n_sess, conv_op_updated)
        self.assertTrue(np.allclose(updated_bias, bias_data))

    def test_cls_layer_select_conv_with_identity(self):
        """
        test cross layer scaling layer selection code when convs have identity nodes in-btw.
        This was observed with TF Slim Mobilenetv2 model
        """
        tf.reset_default_graph()
        inputs = tf.keras.Input(shape=(32, 32, 3,), name="inputs")
        conv1_op = tf.keras.layers.Conv2D(32, (3, 3))(inputs)
        relu_op = tf.nn.relu(conv1_op)
        identity = tf.identity(relu_op)
        conv2_op = tf.keras.layers.Conv2D(32, (3, 3))(identity)
        relu2_op = tf.nn.relu(conv2_op)

        init = tf.global_variables_initializer()
        sess = tf.Session()
        sess.run(init)

        start_op = "inputs"
        output_op = 'Relu_1'

        graph_search = GraphSearchUtils(sess.graph, start_op, output_op)
        layer_groups_as_tf_ops = graph_search.find_layer_groups_to_scale()

        assert len(layer_groups_as_tf_ops) == 1

    def test_high_bias_fold_conv_without_bn(self):
        """
        Test high bias fold with a custom model
        """
        tf.reset_default_graph()
        inputs = tf.keras.Input(shape=(32, 32, 3,))

        conv_op = tf.keras.layers.Conv2D(32, (3, 3))(inputs)
        conv2_op = tf.keras.layers.Conv2D(32, (3, 3))(conv_op)
        # bn_op = tf.keras.layers.BatchNormalization(fused=True)(conv2_op)
        relu_1= tf.nn.relu(conv2_op)

        conv2_op = tf.keras.layers.Conv2D(32, (3, 3))(relu_1)
        bn_op_2 = tf.keras.layers.BatchNormalization(fused=True)(conv2_op)
        relu_2 = tf.nn.relu(bn_op_2)

        init = tf.global_variables_initializer()
        sess = tf.Session()
        sess.run(init)
        np.random.seed(0)

        old_conv_op = sess.graph.get_operation_by_name('conv2d/Conv2D')

        b_shape = BiasUtils.get_shape(old_conv_op)
        numpy_data = np.random.rand(b_shape[0])
        BiasUtils.update_bias_for_op(sess, old_conv_op, numpy_data)
        graph_eval.initialize_uninitialized_vars(sess)

        # save and load the updated graph after high bias fold update
        n_sess = aimet_tensorflow.utils.graph_saver.save_and_load_graph('./test_update', sess)

        conv_op = n_sess.graph.get_operation_by_name('conv2d/Conv2D')
        bias_data= BiasUtils.get_bias_as_numpy_data(n_sess, conv_op)

        new_sess = equalize_model(n_sess, conv_op.inputs[0].op.name, relu_2.op.name)

        new_conv_op = new_sess.graph.get_operation_by_name('conv2d/Conv2D')
        bias_data_after_fold = BiasUtils.get_bias_as_numpy_data(new_sess, new_conv_op)

        for i in range(len(bias_data_after_fold)):
            self.assertTrue(bias_data_after_fold[i] <= bias_data[i])

    def test_equalize_model_multi_input(self):

        """
        Test bn fold with multiple input nodes
        """

        tf.reset_default_graph()
        input1 = tf.keras.Input(name='input1', shape=(10, 10, 3))
        input2 = tf.keras.Input(name='input2', shape=(12, 12, 3))
        x1 = tf.keras.layers.Conv2D(8, (1, 1), name='conv1a',
                                    kernel_initializer=tf.random_uniform_initializer(-1, 1),
                                    bias_initializer='random_uniform')(input1)
        x2 = tf.keras.layers.Conv2D(8, (3, 3), name='conv1b',
                                    kernel_initializer=tf.random_uniform_initializer(-1, 1),
                                    bias_initializer='random_uniform')(x1)
        x3 = tf.keras.layers.Conv2D(8, (3, 3), name='conv1c',
                                    kernel_initializer=tf.random_uniform_initializer(-1, 1),
                                    bias_initializer='random_uniform')(input2)
        x4 = tf.keras.layers.Conv2D(8, (3, 3), name='conv1d',
                                    kernel_initializer=tf.random_uniform_initializer(-1, 1),
                                    bias_initializer='random_uniform')(x3)
        x = tf.keras.layers.add([x2, x4])
        conv2_op = tf.keras.layers.Conv2D(32, (3, 3))(x)
        bn_op = tf.keras.layers.BatchNormalization(fused=True)(conv2_op)
        _ = tf.nn.relu(bn_op)

        init = tf.global_variables_initializer()
        sess = tf.Session()
        sess.run(init)

        conv_1b_before_equalize = sess.graph.get_operation_by_name('conv1b/Conv2D')
        conv_1b_bias_data_before_fold = BiasUtils.get_bias_as_numpy_data(sess, conv_1b_before_equalize)
        conv_1d_before_equalize = sess.graph.get_operation_by_name('conv1d/Conv2D')
        conv_1d_bias_data_before_fold = BiasUtils.get_bias_as_numpy_data(sess, conv_1d_before_equalize)

        new_sess = equalize_model(sess, ["input1", "input2"], 'Relu')

        conv_1b_after_equalize = new_sess.graph.get_operation_by_name('conv1b/Conv2D')
        conv_1b_bias_data_after_fold = BiasUtils.get_bias_as_numpy_data(new_sess, conv_1b_after_equalize)
        conv_1d_after_equalize = new_sess.graph.get_operation_by_name('conv1d/Conv2D')
        conv_1d_bias_data_after_fold = BiasUtils.get_bias_as_numpy_data(new_sess, conv_1d_after_equalize)

        for i in range(len(conv_1b_bias_data_after_fold)):
            self.assertTrue(conv_1b_bias_data_after_fold[i] <= conv_1b_bias_data_before_fold[i])

        for i in range(len(conv_1d_bias_data_after_fold)):
            self.assertTrue(conv_1d_bias_data_after_fold[i] <= conv_1d_bias_data_before_fold[i])

    def test_equalize_with_custom_model_no_bias(self):
        """
        Test equalize with a custom model with conv without bias param
        """
        tf.reset_default_graph()

        sess = tf.Session(graph=tf.get_default_graph())

        with sess.as_default():
            inputs = tf.keras.Input(shape=(32, 32, 3,))

            conv_op = tf.keras.layers.Conv2D(32, (3, 3), use_bias=False)(inputs)
            bn_op = tf.keras.layers.BatchNormalization(fused=True)(conv_op)
            relu_1= tf.nn.relu(bn_op)

            conv2_op = tf.keras.layers.Conv2D(32, (3, 3), use_bias=False)(relu_1)
            bn_op_2 = tf.keras.layers.BatchNormalization(fused=True)(conv2_op, training=False)
            relu_2 = tf.nn.relu(bn_op_2)

            init = tf.global_variables_initializer()
            sess.run(init)

            old_conv_op = sess.graph.get_operation_by_name('conv2d/Conv2D')
            self.assertTrue(BiasUtils.is_bias_none(old_conv_op))

            conv_op = sess.graph.get_operation_by_name('conv2d/Conv2D')
            new_sess = equalize_model(sess, conv_op.inputs[0].op.name, 'Relu_1')

            new_conv_op = new_sess.graph.get_operation_by_name('conv2d/Conv2D')
            bias = BiasUtils.get_bias_as_numpy_data(new_sess, new_conv_op)
            self.assertFalse(BiasUtils.is_bias_none(new_conv_op))

    def test_equalize_fold_forward(self):
        """
        Test equalize on a model with a forward bn fold
        """
        tf.reset_default_graph()
        inputs = tf.keras.Input(shape=(32, 32, 3,), name="inputs")
        conv_op = tf.keras.layers.Conv2D(32, (3, 3))(inputs)
        r_op = tf.nn.relu(conv_op)
        bn_op = tf.keras.layers.BatchNormalization(fused=True)(r_op)
        conv2_op = tf.keras.layers.Conv2D(32, (3, 3))(bn_op)
        conv3_op = tf.keras.layers.Conv2D(32, (3, 3))(conv2_op)
        _ = tf.nn.relu(conv3_op)

        init = tf.global_variables_initializer()
        sess = tf.Session(graph = tf.get_default_graph())
        sess.run(init)
        old_conv_op = sess.graph.get_operation_by_name('conv2d/Conv2D')
        conv_bias_data_before_fold = BiasUtils.get_bias_as_numpy_data(sess, old_conv_op)

        conv_op = sess.graph.get_operation_by_name('conv2d/Conv2D')

        new_sess = equalize_model(sess, conv_op.inputs[0].op.name, 'Relu_1')
        new_conv_op = new_sess.graph.get_operation_by_name('conv2d/Conv2D')
        self.assertFalse(BiasUtils.is_bias_none(new_conv_op))
        conv_bias_data_after_fold = BiasUtils.get_bias_as_numpy_data(new_sess, new_conv_op)

        for i in range(len(conv_bias_data_before_fold)):
            self.assertTrue(conv_bias_data_before_fold[i] <= conv_bias_data_after_fold[i])
