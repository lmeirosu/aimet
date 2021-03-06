# /usr/bin/env python3.5
# -*- mode: python -*-
# =============================================================================
#  @@-COPYRIGHT-START-@@
#
#  Copyright (c) 2019-2020, Qualcomm Innovation Center, Inc. All rights reserved.
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
""" Module to test TF utils """
import unittest
import numpy as np

import tensorflow as tf
import tensorflow.contrib.slim as slim
from keras.applications.vgg16 import VGG16
from keras.applications.resnet50 import ResNet50

from aimet_common.utils import AimetLogger
from aimet_tensorflow.utils.common import get_ordered_ops, create_input_feed_dict, \
    iter_first_x, get_ordered_conv_linears, get_training_tensors
from aimet_tensorflow.utils.graph_saver import wrapper_func
from aimet_tensorflow.examples.test_models import single_residual, multiple_input_model, \
    model_with_multiple_training_tensors
from aimet_tensorflow.utils.op.conv import WeightTensorUtils, BiasUtils, get_output_activation_shape
from aimet_tensorflow.utils.op.fusedbatchnorm import BNUtils

from aimet_tensorflow.utils.graph_saver import save_and_load_graph

logger = AimetLogger.get_area_logger(AimetLogger.LogAreas.Test)


class TestTrainingExtensionsTfUtils(unittest.TestCase):
    """ Unittest class for testing Tf Utils """

    def test_wrapper_func_second_arg_without_args(self):
        """
        test wrapper_func without any arguments, expect ValueError
        """
        def dummy_eval_func():
            return 1

        dummy_eval_func = wrapper_func(dummy_eval_func)

        # calling dummy_eval_func without any arguments
        with self.assertRaises(ValueError):
            dummy_eval_func()

    def test_wrapper_func_second_arg_with_sess(self):
        """
        test wrapper_func with second argument tf.Session, expect ValueError
        """
        def dummy_eval_func(model, _):
            return model

        g = tf.Graph()
        with g.as_default():
            _ = VGG16(weights=None, input_shape=(224, 224, 3))
            init = tf.global_variables_initializer()

        sess = tf.Session(graph=g)
        sess.run(init)

        dummy_eval_func = wrapper_func(dummy_eval_func)

        # calling dummy_eval_func with first random argument, and second argument tf.Session
        self.assertRaises(ValueError, lambda: dummy_eval_func('test', sess))

        sess.close()

    def test_wrapper_func_first_arg_with_sess(self):
        """
        test wrapper_func with first argument tf.Session
        test to see if the provides session and updated session should be different or not
        """
        def dummy_eval_func(model, _):
            return model

        g = tf.Graph()
        with g.as_default():
            _ = VGG16(weights=None, input_shape=(224, 224, 3))
            init = tf.global_variables_initializer()

        sess = tf.Session(graph=g)
        sess.run(init)

        dummy_eval_func = wrapper_func(dummy_eval_func)

        # calling dummy_eval_func with tf.Session first argument
        updated_sess = dummy_eval_func(sess, 'test')
        self.assertNotEqual(sess, updated_sess)

        sess.close()

    def test_get_ordered_ops_with_single_residual(self):
        """
        test get_op with simple single residual model
        """
        g = tf.Graph()

        with g.as_default():
            single_residual()

        ordered_ops = get_ordered_ops(g, ['input_1'])

        self.assertTrue(ordered_ops.index(g.get_operation_by_name('conv2d_4/Conv2D')) >
                        ordered_ops.index(g.get_operation_by_name('conv2d_1/Conv2D')))

    def test_get_ordered_ops_with_resnet50(self):
        """
        test get_ordered_operations with Resnet50 model
        """
        g = tf.Graph()

        with g.as_default():
            _ = ResNet50(weights=None)

            inp_tensor = tf.get_variable('inp_tensor', shape=[1, 20, 5, 5],
                                         initializer=tf.random_normal_initializer())

            filter_tensor = tf.get_variable('filter_tensor', shape=[5, 5, 20, 50],
                                            initializer=tf.random_normal_initializer())

            # add random conv, which is not part of forward pass
            # pylint: disable=no-member
            _ = tf.nn.conv2d(input=inp_tensor, filter=filter_tensor, strides=[1, 1, 1, 1], padding='VALID',
                             data_format="NCHW", name='dangling/Conv2D')

        ordered_ops = get_ordered_ops(g, ['input_1'])

        for op in ordered_ops:
            if op.type == 'Conv2D':
                print(op.name)

        self.assertTrue(ordered_ops.index(g.get_operation_by_name('res2a_branch2b/convolution')) >
                        ordered_ops.index(g.get_operation_by_name('res2a_branch1/convolution')))

        self.assertTrue(ordered_ops.index(g.get_operation_by_name('activation_4/Relu')) >
                        ordered_ops.index(g.get_operation_by_name('add_1/add')))

        self.assertTrue(ordered_ops.index(g.get_operation_by_name('res2a_branch2a/BiasAdd')) >
                        ordered_ops.index(g.get_operation_by_name('res2a_branch2a/convolution')))

        self.assertTrue(g.get_operation_by_name('dangling/Conv2D') not in ordered_ops)

    def test_get_ordered_ops_with_multiple_inputs(self):
        """
        test get_ordered_operations with multiple inputs
        """

        g = tf.Graph()

        with g.as_default():
            multiple_input_model()

        ordered_ops = get_ordered_ops(g, ['input2', 'input1'])

        self.assertTrue(ordered_ops.index(g.get_operation_by_name('conv1b/Conv2D')) >
                        ordered_ops.index(g.get_operation_by_name('input2')))

        self.assertTrue(ordered_ops.index(g.get_operation_by_name('conv1a/Conv2D')) >
                        ordered_ops.index(g.get_operation_by_name('input1')))

        self.assertTrue(ordered_ops.index(g.get_operation_by_name('add/add')) >
                        ordered_ops.index(g.get_operation_by_name('input1')))

        self.assertTrue(ordered_ops.index(g.get_operation_by_name('add/add')) >
                        ordered_ops.index(g.get_operation_by_name('input2')))

    def test_create_input_feed_dict(self):
        """
        test create_input_feed_dict
        """

        # 1) input_batch_data numpy array
        g = tf.Graph()
        with g.as_default():
            _ = single_residual()

        input_data = np.random.rand(1, 16, 16, 3)
        feed_dict = create_input_feed_dict(graph=g, input_op_names_list=['input_1'], input_data=input_data)
        self.assertEqual(feed_dict[g.get_tensor_by_name('input_1:0')].shape, input_data.shape)

        tf.reset_default_graph()

        # 2) input_batch_data List of numpy array
        g = tf.Graph()
        with g.as_default():
            multiple_input_model()

        input_data = list()
        input_data.append(np.random.rand(10, 10, 3))
        input_data.append(np.random.rand(12, 12, 3))
        feed_dict = create_input_feed_dict(graph=g, input_op_names_list=['input1', 'input2'],
                                           input_data=input_data)

        self.assertEqual(feed_dict[g.get_tensor_by_name('input1:0')].shape, input_data[0].shape)
        self.assertEqual(feed_dict[g.get_tensor_by_name('input2:0')].shape, input_data[1].shape)

        tf.reset_default_graph()

        # 3) input_batch_data Tuple of numpy array
        g = tf.Graph()
        with g.as_default():
            multiple_input_model()

        input_data = (np.random.rand(10, 10, 3), np.random.rand(12, 12, 3))

        feed_dict = create_input_feed_dict(graph=g, input_op_names_list=['input1', 'input2'],
                                           input_data=input_data)

        self.assertEqual(feed_dict[g.get_tensor_by_name('input1:0')].shape, input_data[0].shape)
        self.assertEqual(feed_dict[g.get_tensor_by_name('input2:0')].shape, input_data[1].shape)
        tf.reset_default_graph()

        # 3) input_batch_data and input_op_names mismatch
        g = tf.Graph()
        with g.as_default():
            multiple_input_model()

        input_data = (np.random.rand(10, 10, 3))

        self.assertRaises(ValueError, lambda: create_input_feed_dict(graph=g,
                                                                     input_op_names_list=['input1', 'input2'],
                                                                     input_data=input_data))
        tf.reset_default_graph()

        g = tf.Graph()
        with g.as_default():
            model_with_multiple_training_tensors()
        input_data = (np.random.rand(32, 32, 3))
        feed_dict = create_input_feed_dict(graph=g, input_op_names_list=['input_1'],
                                           input_data=input_data, training=True)
        keras_learning_phase_tensor = g.get_tensor_by_name('keras_learning_phase:0')
        is_training_tensor = g.get_tensor_by_name('is_training:0')
        is_training_2_tensor = g.get_tensor_by_name('is_training_2:0')
        self.assertEqual(feed_dict[keras_learning_phase_tensor], True)
        self.assertEqual(feed_dict[is_training_tensor], True)
        self.assertEqual(feed_dict[is_training_2_tensor], True)
        tf.reset_default_graph()

    def test_iter_first_x(self):
        """ Test iter_first_x generator for creating a dataset generator """

        tf.reset_default_graph()
        sess = tf.Session()
        with sess.graph.as_default():
            dataset = tf.data.Dataset.from_tensor_slices([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
            dataset_iterator = iter_first_x(dataset, num_batches=5)

        for i, data in enumerate(dataset_iterator):
            self.assertEqual(i, data)       # Data has not been batched, so each element should be returned individually
            self.assertTrue(i < 5)          # Check that iterator stops at the correct point

        with sess.graph.as_default():
            dataset = tf.data.Dataset.from_tensor_slices([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
            dataset = dataset.batch(2)
            dataset_iterator = iter_first_x(dataset, num_batches=5)

        for i, data in enumerate(dataset_iterator):
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0], 2*i)
            self.assertEqual(data[1], 2*i+1)

        # Test that trying to extract more data than possible from the dataset is handled
        # since tensorflow OutOfRangeError is converted to StopIteration
        with sess.graph.as_default():
            dataset_iterator = iter_first_x(dataset, num_batches=6)

        for i, data in enumerate(dataset_iterator):
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0], 2*i)
            self.assertEqual(data[1], 2*i+1)

        sess.close()

    def test_update_to_weight_tensor_with_load_var(self):
        """
        tests update to weight tensor of conv op using tf variable load api
        :return:
        """

        # create conv op
        tf.reset_default_graph()
        inputs = tf.keras.Input(shape=(32, 32, 3,))
        _ = tf.keras.layers.Conv2D(32, (3, 3), kernel_initializer=tf.random_uniform_initializer(-1, 2))(inputs)

        init = tf.global_variables_initializer()
        sess = tf.Session()
        sess.run(init)

        conv_op = sess.graph.get_operation_by_name('conv2d/Conv2D')

        original_weights = WeightTensorUtils.get_tensor_as_numpy_data(sess, conv_op)

        # add dummy weight tensor data
        np.random.seed(0)
        w_shape = WeightTensorUtils.get_tensor_shape(conv_op)
        numpy_data = np.random.rand(3, w_shape[1], w_shape[2], w_shape[3])

        # send in numpy data to overwrite previous value
        WeightTensorUtils.update_tensor_for_op(sess, conv_op, numpy_data)

        updated_weight_tensor = WeightTensorUtils.get_tensor_as_numpy_data(sess, conv_op)

        # validate they are not the same
        self.assertFalse(np.allclose(original_weights, updated_weight_tensor))
        self.assertTrue(np.allclose(numpy_data, updated_weight_tensor))
        sess.close()

    def test_update_to_bias_with_load_var(self):
        """
        tests update to bias param of conv op using tf variable load api
        :return:
        """

        # create conv op
        tf.reset_default_graph()
        inputs = tf.keras.Input(shape=(32, 32, 3,))
        conv_op = tf.keras.layers.Conv2D(32, (3, 3),
                                   kernel_initializer=tf.random_uniform_initializer(-1, 2))(inputs)

        bn_op = tf.keras.layers.BatchNormalization(fused=True)(conv_op)
        _ = tf.nn.relu(bn_op)

        init = tf.global_variables_initializer()
        sess = tf.Session()
        sess.run(init)

        conv_op = sess.graph.get_operation_by_name('conv2d/Conv2D')

        original_bias = BiasUtils.get_bias_as_numpy_data(sess, conv_op)

        # add dummy weight tensor data
        np.random.seed(0)
        b_shape = BiasUtils.get_shape(conv_op)
        numpy_data = np.random.rand(b_shape[0])

        # send in numpy data to overwrite previous value
        BiasUtils.update_bias_for_op(sess, conv_op, numpy_data)

        updated_bias = BiasUtils.get_bias_as_numpy_data(sess, conv_op)

        # validate they are not the same
        self.assertFalse(np.allclose(original_bias, updated_bias))
        self.assertTrue(np.allclose(numpy_data, updated_bias))
        sess.close()

    def test_bias_add_with_conv(self):
        """
        Test bias add on conv op
        :return:
        """

        tf.reset_default_graph()
        inputs = tf.keras.Input(shape=(32, 32, 3,), name="inputs")
        # create a conv without bias param
        conv_op = tf.keras.layers.Conv2D(32, (3, 3), use_bias=False)(inputs)
        bn_op = tf.keras.layers.BatchNormalization(fused=True)(conv_op)
        # pylint: disable=no-member
        _ = tf.nn.relu(bn_op)

        init = tf.global_variables_initializer()
        sess = tf.Session()
        sess.run(init)

        conv_op = sess.graph.get_operation_by_name('conv2d/Conv2D')
        self.assertTrue(BiasUtils.is_bias_none(conv_op))

        # new_sess = BiasUtils.initialize_model_with_bias(sess)
        shape = BiasUtils.get_shape(conv_op)
        numpy_data = np.random.rand(shape[0])
        BiasUtils.update_bias_for_op(sess, conv_op, bias_as_numpy_array=numpy_data)
        new_sess = save_and_load_graph('./temp_bn_fold', sess)
        conv_op = new_sess.graph.get_operation_by_name('conv2d/Conv2D')
        bias_as_numpy_data = BiasUtils.get_bias_as_numpy_data(new_sess, conv_op)

        assert(not BiasUtils.is_bias_none(conv_op))
        new_sess.close()

    def test_bias_update_to_dense(self):
        """
        test bias correction on matmul layer
        :return:
        """
        tf.reset_default_graph()

        inputs = tf.keras.Input(shape=(32, 32, 3,))
        x = tf.keras.layers.Flatten()(inputs)
        dense = tf.keras.layers.Dense(2, use_bias=False, activation=tf.nn.softmax, name="single_residual")(x)
        # pylint: disable=no-member
        _ = tf.nn.relu(dense)

        init = tf.global_variables_initializer()
        sess = tf.Session(graph=tf.get_default_graph())
        sess.run(init)

        dense_op = sess.graph.get_operation_by_name('single_residual/MatMul')
        self.assertTrue(BiasUtils.is_bias_none(dense_op))

        new_sess = BiasUtils.initialize_model_with_bias(sess)

        dense_op = new_sess.graph.get_operation_by_name('single_residual/MatMul')
        self.assertTrue(not BiasUtils.is_bias_none(dense_op))
        new_sess.close()

    def test_get_ordered_conv_linears(self):
        """
        Test get_ordered_conv_linears
        """
        tf.reset_default_graph()
        inputs = tf.keras.Input(shape=(32, 32, 3,))

        conv_op = tf.keras.layers.Conv2D(32, (3, 3))(inputs)
        # pylint: disable=no-member
        relu_1 = tf.nn.relu(conv_op)

        conv2_op = tf.keras.layers.Conv2D(32, (3, 3))(relu_1)
        _ = tf.nn.relu(conv2_op)

        init = tf.global_variables_initializer()
        sess = tf.Session(graph=tf.get_default_graph())
        sess.run(init)

        conv_op = sess.graph.get_operation_by_name('conv2d/Conv2D')

        # check if we get ordered list
        input_op = conv_op.inputs[0].op.name
        selected_ops = get_ordered_conv_linears(sess, [input_op])

        self.assertEqual(2, len(selected_ops))
        conv_op = sess.graph.get_operation_by_name('conv2d/Conv2D')
        conv_1_op = sess.graph.get_operation_by_name('conv2d_1/Conv2D')
        self.assertEqual(selected_ops[0], conv_op)
        self.assertEqual(selected_ops[1], conv_1_op)

    def test_get_training_tensors(self):
        """ Test for obtaining all training tensors in a graph """
        tf.reset_default_graph()
        _ = model_with_multiple_training_tensors()
        training_tensors = get_training_tensors(tf.get_default_graph())
        self.assertEqual(3, len(training_tensors))

    def test_param_read_bn_training_true(self):
        """
        test we can fetch the params from a bn op that has training set to true.
        :return:
        """
        tf.reset_default_graph()

        sess = tf.Session(graph=tf.get_default_graph())

        with sess.as_default():
            inputs = tf.keras.Input(shape=(32, 32, 3,))

            conv_op = tf.keras.layers.Conv2D(32, (3, 3), use_bias=False)(inputs)
            bn_op = tf.keras.layers.BatchNormalization(fused=True)(conv_op, training=True)

            init = tf.global_variables_initializer()
            sess.run(init)

            moving_mean = BNUtils.get_moving_mean_as_numpy_data(sess, bn_op.op)

            moving_var = BNUtils.get_moving_variance_as_numpy_data(sess, bn_op.op)

            assert moving_mean is not None
            assert moving_var is not None

        sess.close()

    def test_param_read_keras_bn_op_default(self):
        """
        Test we can fetch the params from a bn op with no explicit setting of training flag
        :return:
        """
        tf.reset_default_graph()
        sess = tf.Session(graph=tf.get_default_graph())

        with sess.as_default():
            inputs = tf.keras.Input(shape=(32, 32, 3,))
            conv_op = tf.keras.layers.Conv2D(32, (3, 3), use_bias=False)(inputs)
            bn = tf.keras.layers.BatchNormalization(fused=True)(conv_op)

            init = tf.global_variables_initializer()
            sess.run(init)
            # _ = tf.summary.FileWriter('./keras_model_bn_op', sess.graph)

            # we use the bn op with is_training attribute set to false
            bn_op_tensor = sess.graph.get_tensor_by_name('batch_normalization/cond/FusedBatchNormV3_1:0')
            moving_mean = BNUtils.get_moving_mean_as_numpy_data(sess, bn_op_tensor.op)
            moving_var = BNUtils.get_moving_variance_as_numpy_data(sess, bn_op_tensor.op)
            beta = BNUtils.get_beta_as_numpy_data(sess, bn_op_tensor.op)
            gamma = BNUtils.get_gamma_as_numpy_data(sess, bn_op_tensor.op)
            assert beta is not None
            assert gamma is not None
            assert moving_mean is not None
            assert moving_var is not None

        sess.close()

    def test_param_read_keras_bn_training_true(self):
        """
        test we can fetch the params from a bn op that has training set to true.
        :return:
        """
        tf.reset_default_graph()
        sess = tf.Session(graph=tf.get_default_graph())

        with sess.as_default():
            inputs = tf.keras.Input(shape=(32, 32, 3,))
            conv_op = tf.keras.layers.Conv2D(32, (3, 3), use_bias=False)(inputs)
            bn_op_tensor = tf.keras.layers.BatchNormalization(fused=True, name="bn_op_1/")(conv_op, training=True)

            init = tf.global_variables_initializer()
            sess.run(init)

            moving_mean = BNUtils.get_moving_mean_as_numpy_data(sess, bn_op_tensor.op)
            moving_var = BNUtils.get_moving_variance_as_numpy_data(sess, bn_op_tensor.op)
            beta = BNUtils.get_beta_as_numpy_data(sess, bn_op_tensor.op)
            gamma = BNUtils.get_gamma_as_numpy_data(sess, bn_op_tensor.op)
            assert beta is not None
            assert gamma is not None
            assert moving_mean is not None
            assert moving_var is not None

        sess.close()

    def test_param_read_keras_bn_training_false(self):
        """
        test we can fetch the params from a bn op that has training set to false.
        :return:
        """
        tf.reset_default_graph()
        sess = tf.Session(graph=tf.get_default_graph())

        with sess.as_default():
            inputs = tf.keras.Input(shape=(32, 32, 3,))
            conv_op = tf.keras.layers.Conv2D(32, (3, 3), use_bias=False)(inputs)
            bn_op_tensor = tf.keras.layers.BatchNormalization(fused=True, name="bn_op_1")(conv_op, training=False)

            init = tf.global_variables_initializer()
            sess.run(init)

            moving_mean = BNUtils.get_moving_mean_as_numpy_data(sess, bn_op_tensor.op)
            moving_var = BNUtils.get_moving_variance_as_numpy_data(sess, bn_op_tensor.op)
            beta = BNUtils.get_beta_as_numpy_data(sess, bn_op_tensor.op)
            gamma = BNUtils.get_gamma_as_numpy_data(sess, bn_op_tensor.op)

            assert beta is not None
            assert gamma is not None
            assert moving_mean is not None
            assert moving_var is not None

        sess.close()

    def test_with_keras_resnet50_with_weights(self):
        """
        Test to replicate SFTI issue reported with Keras Resnet50 BN layer param extraction
        :return:
        """
        from tensorflow.python.keras.applications.resnet import ResNet50
        tf.keras.backend.clear_session()
        _ = ResNet50(weights='imagenet', input_shape=(224, 224, 3))
        sess = tf.keras.backend.get_session()

        # error reported by SFTI
        # tensorflow.python.framework.errors_impl.InvalidArgumentError
        # (0) Invalid argument: You must feed a value for placeholder tensor
        # 'Placeholder_5' with dtype float and shape [?]
        with sess.as_default():
            bn_op_name = "conv1_bn/cond/FusedBatchNormV3_1"
            bn_op = sess.graph.get_operation_by_name(bn_op_name)
            moving_mean = BNUtils.get_moving_mean_as_numpy_data(sess, bn_op)
            moving_var = BNUtils.get_moving_variance_as_numpy_data(sess, bn_op)
            beta = BNUtils.get_beta_as_numpy_data(sess, bn_op)
            gamma = BNUtils.get_gamma_as_numpy_data(sess, bn_op)

            assert beta is not None
            assert gamma is not None
            assert moving_mean is not None
            assert moving_var is not None

        sess.close()

    def test_with_tf_bn_op(self):
        """
        Test with TF BN op
        :return:
        """
        tf.reset_default_graph()
        sess = tf.Session(graph=tf.get_default_graph())
        inp = tf.placeholder(tf.float32, [1, 32, 32, 3])
        net = tf.layers.conv2d(inp, 32, [3, 3])
        _ = tf.compat.v1.layers.batch_normalization(net)

        # _ = tf.summary.FileWriter('./keras_model_bn_op', sess.graph)
        init = tf.global_variables_initializer()
        sess.run(init)
        bn_op = sess.graph.get_operation_by_name('batch_normalization/FusedBatchNormV3')
        moving_mean = BNUtils.get_moving_mean_as_numpy_data(sess, bn_op)
        moving_var = BNUtils.get_moving_variance_as_numpy_data(sess, bn_op)
        beta = BNUtils.get_beta_as_numpy_data(sess, bn_op)
        gamma = BNUtils.get_gamma_as_numpy_data(sess, bn_op)

        assert beta is not None
        assert gamma is not None
        assert moving_mean is not None
        assert moving_var is not None

    def test_with_slim_bn_op(self):
        """
        Test with Tf Slim BN op
        :return:
        """
        tf.reset_default_graph()
        sess = tf.Session(graph=tf.get_default_graph())
        inp = tf.placeholder(tf.float32, [1, 32, 32, 3])
        net = slim.conv2d(inp, 32, [3, 3])
        _ = slim.batch_norm(net, decay=.7, epsilon=.65, is_training=True)

        init = tf.global_variables_initializer()
        sess.run(init)
        # _ = tf.summary.FileWriter('./keras_model_bn_op', sess.graph)
        bn_op = sess.graph.get_operation_by_name('BatchNorm/FusedBatchNormV3')
        moving_mean = BNUtils.get_moving_mean_as_numpy_data(sess, bn_op)
        moving_var = BNUtils.get_moving_variance_as_numpy_data(sess, bn_op)
        beta = BNUtils.get_beta_as_numpy_data(sess, bn_op)
        gamma = BNUtils.get_gamma_as_numpy_data(sess, bn_op)
        assert beta is not None
        assert gamma is not None
        assert moving_mean is not None
        assert moving_var is not None

    def test_get_output_activation_shape(self):
        """Test for getting output activation shapes"""

        # 1) dynamic shape

        graph = tf.Graph()
        filter_data = np.ones([5, 5, 3, 32], dtype=np.float32)

        with graph.as_default():
            input_tensor = tf.placeholder(tf.float32, [1, None, None, None], 'input')

            filter_tensor = tf.Variable(initial_value=filter_data, name='filter_tensor', dtype=tf.float32)

            _ = tf.nn.conv2d(input=input_tensor, filter=filter_tensor, padding='SAME', strides=[1, 1, 1, 1],
                             data_format="NCHW", name='Conv2D_1')

            init = tf.global_variables_initializer()

        sess = tf.Session(graph=graph)
        sess.run(init)

        conv_op = sess.graph.get_operation_by_name('Conv2D_1')
        output_shape = get_output_activation_shape(sess=sess, op=conv_op, input_op_names=['input'],
                                                   input_shape=(1, 3, 10, 10))

        batch_size, channels, activations_h, activations_w = output_shape

        self.assertEqual(activations_h, 10)
        self.assertEqual(activations_w, 10)
        self.assertEqual(channels, 32)

        sess.close()

        # 2) static shape

        graph = tf.Graph()
        input_data = np.ones([1, 3, 10, 10], dtype=np.float32)
        filter_data = np.ones([5, 5, 3, 32], dtype=np.float32)

        with graph.as_default():
            input_tensor = tf.Variable(initial_value=input_data, name='input', dtype=tf.float32)
            filter_tensor = tf.Variable(initial_value=filter_data, name='filter_tensor', dtype=tf.float32)

            _ = tf.nn.conv2d(input=input_tensor, filter=filter_tensor, padding='SAME', strides=[1, 1, 1, 1],
                             data_format="NCHW", name='Conv2D_1')

            init = tf.global_variables_initializer()

        sess = tf.Session(graph=graph)
        sess.run(init)

        conv_op = sess.graph.get_operation_by_name('Conv2D_1')
        output_shape = get_output_activation_shape(sess=sess, op=conv_op, input_op_names=['input'],
                                                   input_shape=(1, 3, 10, 10))

        batch_size, channels, activations_h, activations_w = output_shape
        self.assertEqual(activations_h, 10)
        self.assertEqual(activations_w, 10)
        self.assertEqual(channels, 32)

        sess.close()

    def test_get_output_activation_shape_channels_last(self):
        """Test for getting output activation shapes for channels_last format"""

        # 1) dynamic shape

        graph = tf.Graph()
        filter_data = np.ones([5, 5, 3, 32], dtype=np.float32)

        with graph.as_default():
            input_tensor = tf.placeholder(tf.float32, [1, None, None, None], 'input')

            filter_tensor = tf.Variable(initial_value=filter_data, name='filter_tensor', dtype=tf.float32)

            _ = tf.nn.conv2d(input=input_tensor, filter=filter_tensor, padding='SAME', strides=[1, 1, 1, 1],
                             data_format="NHWC", name='Conv2D_1')

            init = tf.global_variables_initializer()

        sess = tf.Session(graph=graph)
        sess.run(init)

        conv_op = sess.graph.get_operation_by_name('Conv2D_1')
        output_shape = get_output_activation_shape(sess=sess, op=conv_op, input_op_names=['input'],
                                                   input_shape=(1, 10, 10, 3))

        batch_size, channels, activations_h, activations_w = output_shape

        self.assertEqual(activations_h, 10)
        self.assertEqual(activations_w, 10)
        self.assertEqual(channels, 32)

        sess.close()

        # 2) static shape

        graph = tf.Graph()
        # channels_last format
        input_data = np.ones([1, 10, 10, 3], dtype=np.float32)
        filter_data = np.ones([5, 5, 3, 32], dtype=np.float32)

        with graph.as_default():
            input_tensor = tf.Variable(initial_value=input_data, name='input', dtype=tf.float32)
            filter_tensor = tf.Variable(initial_value=filter_data, name='filter_tensor', dtype=tf.float32)

            _ = tf.nn.conv2d(input=input_tensor, filter=filter_tensor, padding='SAME', strides=[1, 1, 1, 1],
                             data_format="NHWC", name='Conv2D_1')

            init = tf.global_variables_initializer()

        sess = tf.Session(graph=graph)
        sess.run(init)

        conv_op = sess.graph.get_operation_by_name('Conv2D_1')
        output_shape = get_output_activation_shape(sess=sess, op=conv_op, input_op_names=['input'],
                                                   input_shape=(1, 10, 10, 3))

        batch_size, channels, activations_h, activations_w = output_shape
        self.assertEqual(activations_h, 10)
        self.assertEqual(activations_w, 10)
        self.assertEqual(channels, 32)

        sess.close()
