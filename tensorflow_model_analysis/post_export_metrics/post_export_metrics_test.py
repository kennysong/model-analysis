# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Test for post export metrics.

Note that we actually train and export models within these tests.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os


import apache_beam as beam
from apache_beam.testing import util
import numpy as np
import tensorflow as tf
from tensorflow_model_analysis.api import model_eval_lib
from tensorflow_model_analysis.api import tfma_unit
from tensorflow_model_analysis.eval_saved_model import testutil
from tensorflow_model_analysis.eval_saved_model.example_trainers import dnn_classifier
from tensorflow_model_analysis.eval_saved_model.example_trainers import dnn_regressor
from tensorflow_model_analysis.eval_saved_model.example_trainers import fixed_prediction_classifier
from tensorflow_model_analysis.eval_saved_model.example_trainers import fixed_prediction_classifier_extra_fields
from tensorflow_model_analysis.eval_saved_model.example_trainers import fixed_prediction_classifier_identity_label
from tensorflow_model_analysis.eval_saved_model.example_trainers import fixed_prediction_estimator
from tensorflow_model_analysis.eval_saved_model.example_trainers import fixed_prediction_estimator_extra_fields
from tensorflow_model_analysis.eval_saved_model.example_trainers import linear_classifier
from tensorflow_model_analysis.eval_saved_model.example_trainers import linear_regressor
from tensorflow_model_analysis.eval_saved_model.example_trainers import multi_head
from tensorflow_model_analysis.evaluators import metrics_and_plots_evaluator
from tensorflow_model_analysis.post_export_metrics import post_export_metrics
import tensorflow_model_analysis.post_export_metrics.metric_keys as metric_keys
from tensorflow_model_analysis.proto import metrics_for_slice_pb2

# Seed that returns '1' for the first 14 calls to Poisson(1). This means that
# the bootstrap samples generated for small test sets should a) be deterministic
# and b) match the values seen when not computing uncertainty.
_MAGIC_SEED = 857586


class PostExportMetricsTest(testutil.TensorflowModelAnalysisTest):

  num_bootstrap_samples = 1  # Set to number > 1 to test uncertainty.
  deterministic_test_seed = _MAGIC_SEED

  def _getEvalExportDir(self):
    return os.path.join(self._getTempDir(), 'eval_export_dir')

  def _runTestWithCustomCheck(self,
                              examples,
                              eval_export_dir,
                              metrics,
                              custom_metrics_check=None,
                              custom_plots_check=None):
    # make sure we are doing some checks
    self.assertTrue(custom_metrics_check is not None or
                    custom_plots_check is not None)
    serialized_examples = [ex.SerializeToString() for ex in examples]
    eval_shared_model = self.createTestEvalSharedModel(
        eval_saved_model_path=eval_export_dir, add_metrics_callbacks=metrics)
    extractors = model_eval_lib.default_extractors(
        eval_shared_model=eval_shared_model)
    with beam.Pipeline() as pipeline:
      metrics, plots = (
          pipeline
          | 'Create' >> beam.Create(serialized_examples)
          | 'InputsToExtracts' >> model_eval_lib.InputsToExtracts()
          | 'Extract' >> tfma_unit.Extract(extractors=extractors)  # pylint: disable=no-value-for-parameter
          | 'ComputeMetricsAndPlots' >>
          metrics_and_plots_evaluator.ComputeMetricsAndPlots(
              eval_shared_model=eval_shared_model,
              num_bootstrap_samples=self.num_bootstrap_samples,
              random_seed=self.deterministic_test_seed))
      if custom_metrics_check is not None:
        util.assert_that(metrics, custom_metrics_check, label='metrics')
      if custom_plots_check is not None:
        util.assert_that(plots, custom_plots_check, label='plot')

  def _runTest(self, examples, eval_export_dir, metrics, expected_values_dict):

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertDictElementsAlmostEqual(value, expected_values_dict)
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples, eval_export_dir, metrics, custom_metrics_check=check_result)

  def testExampleCountNoStandardKeys(self):
    # Test ExampleCount with a custom Estimator that doesn't have any of the
    # standard PredictionKeys.
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_estimator.simple_fixed_prediction_estimator(
            None, temp_eval_export_dir, output_prediction_key='non_standard'))
    examples = [
        self._makeExample(prediction=5.0, label=5.0),
        self._makeExample(prediction=6.0, label=6.0),
        self._makeExample(prediction=7.0, label=7.0),
    ]
    expected_values_dict = {
        metric_keys.EXAMPLE_COUNT: 3.0,
    }
    self._runTest(examples, eval_export_dir, [
        post_export_metrics.example_count(),
    ], expected_values_dict)

  def testExampleCountEmptyPredictionsDict(self):
    # Test ExampleCount with a custom Estimator that has empty predictions dict.
    # This is possible if the Estimator doesn't return the predictions dict
    # in EVAL mode, but computes predictions and feeds them into the metrics
    # internally.
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_estimator.simple_fixed_prediction_estimator(
            None, temp_eval_export_dir, output_prediction_key=None))
    examples = [
        self._makeExample(prediction=5.0, label=5.0),
        self._makeExample(prediction=6.0, label=6.0),
        self._makeExample(prediction=7.0, label=7.0),
    ]
    expected_values_dict = {
        metric_keys.EXAMPLE_COUNT: 3.0,
    }
    self._runTest(examples, eval_export_dir, [
        post_export_metrics.example_count(),
    ], expected_values_dict)

  def testPostExportMetricsLinearClassifier(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = linear_classifier.simple_linear_classifier(
        None, temp_eval_export_dir)
    examples = [
        self._makeExample(age=3.0, language='english', label=1.0),
        self._makeExample(age=3.0, language='chinese', label=0.0),
        self._makeExample(age=4.0, language='english', label=1.0),
        self._makeExample(age=5.0, language='chinese', label=0.0)
    ]
    expected_values_dict = {
        metric_keys.EXAMPLE_COUNT: 4.0,
        metric_keys.EXAMPLE_WEIGHT: 15.0,
    }
    self._runTest(examples, eval_export_dir, [
        post_export_metrics.example_count(),
        post_export_metrics.example_weight('age')
    ], expected_values_dict)

  def testPostExportMetricsLinearClassifierWithUncertainty(self):
    self.num_bootstrap_samples = 10
    self.deterministic_test_seed = None  # Explicitly disable determinism.
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = linear_classifier.simple_linear_classifier(
        None, temp_eval_export_dir)
    examples = [
        self._makeExample(age=3.0, language='english', label=1.0),
        self._makeExample(age=3.0, language='chinese', label=0.0),
        self._makeExample(age=4.0, language='english', label=1.0),
        self._makeExample(age=5.0, language='chinese', label=0.0)
    ]

    example_count_metric = post_export_metrics.example_count()
    example_weight_metric = post_export_metrics.example_weight('age')

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (_, value) = got[0]
        self.assertIn(metric_keys.EXAMPLE_COUNT, value)
        count_values = value[metric_keys.EXAMPLE_COUNT]
        self.assertAlmostEqual(count_values.unsampled_value, 4.0)
        self.assertIn(metric_keys.EXAMPLE_WEIGHT, value)
        weight_values = value[metric_keys.EXAMPLE_WEIGHT]
        self.assertAlmostEqual(weight_values.unsampled_value, 15.0)
        output_metrics = metrics_for_slice_pb2.MetricsForSlice().metrics
        example_count_metric.populate_stats_and_pop(value, output_metrics)
        example_weight_metric.populate_stats_and_pop(value, output_metrics)
        self.assertProtoEquals(
            """
            double_value {
              value: 4.0
            }
            """, output_metrics[metric_keys.EXAMPLE_COUNT])
        self.assertProtoEquals(
            """
            double_value {
              value: 15.0
            }
            """, output_metrics[metric_keys.EXAMPLE_WEIGHT])

      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [
            example_count_metric,
            example_weight_metric,
        ],
        custom_metrics_check=check_result)

  def testPostExportMetricsWithTag(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = linear_classifier.simple_linear_classifier(
        None, temp_eval_export_dir)
    examples = [
        self._makeExample(age=3.0, language='english', label=1.0),
        self._makeExample(age=3.0, language='chinese', label=0.0),
        self._makeExample(age=4.0, language='english', label=1.0),
        self._makeExample(age=5.0, language='chinese', label=0.0)
    ]
    expected_values_dict = {
        metric_keys.EXAMPLE_COUNT: 4.0,
        metric_keys.EXAMPLE_WEIGHT: 15.0,
        metric_keys.tagged_key(metric_keys.EXAMPLE_COUNT, 'my_tag'): 4.0,
        metric_keys.tagged_key(metric_keys.EXAMPLE_WEIGHT, 'my_tag'): 15.0,
    }
    self._runTest(examples, eval_export_dir, [
        post_export_metrics.example_count(),
        post_export_metrics.example_weight('age'),
        post_export_metrics.example_count(metric_tag='my_tag'),
        post_export_metrics.example_weight('age', metric_tag='my_tag')
    ], expected_values_dict)

  def testPostExportMetricsDNNClassifier(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = dnn_classifier.simple_dnn_classifier(
        None, temp_eval_export_dir)
    examples = [
        self._makeExample(age=3.0, language='english', label=1.0),
        self._makeExample(age=3.0, language='chinese', label=0.0),
        self._makeExample(age=4.0, language='english', label=1.0),
        self._makeExample(age=5.0, language='chinese', label=0.0)
    ]
    expected_values_dict = {
        metric_keys.EXAMPLE_COUNT: 4.0,
        metric_keys.EXAMPLE_WEIGHT: 15.0,
    }
    self._runTest(examples, eval_export_dir, [
        post_export_metrics.example_count(),
        post_export_metrics.example_weight('age'),
    ], expected_values_dict)

  def testPostExportMetricsDNNClassifierMultiClass(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = dnn_classifier.simple_dnn_classifier(
        None, temp_eval_export_dir, n_classes=3)
    examples = [
        self._makeExample(age=3.0, language='english', label=0),
        self._makeExample(age=3.0, language='chinese', label=1),
        self._makeExample(age=4.0, language='english', label=0),
        self._makeExample(age=5.0, language='chinese', label=1),
    ]
    expected_values_dict = {
        metric_keys.EXAMPLE_COUNT: 4.0,
        metric_keys.EXAMPLE_WEIGHT: 15.0,
    }

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertDictElementsAlmostEqual(value, expected_values_dict)
        # Check that AUC was calculated for each class. We can't check the exact
        # values since we don't know the exact prediction of the model.
        self.assertIn(metric_keys.tagged_key(metric_keys.AUC, 'english'), value)
        self.assertIn(metric_keys.tagged_key(metric_keys.AUC, 'chinese'), value)
        self.assertIn(metric_keys.tagged_key(metric_keys.AUC, 'other'), value)
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [
            post_export_metrics.example_count(),
            post_export_metrics.example_weight('age'),
            post_export_metrics.auc(tensor_index=0, metric_tag='english'),
            post_export_metrics.auc(tensor_index=1, metric_tag='chinese'),
            post_export_metrics.auc(tensor_index=2, metric_tag='other'),
        ],
        custom_metrics_check=check_result)

  def testPostExportMetricsMultiClassFixedPrediction(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_classifier_identity_label
        .simple_fixed_prediction_classifier_identity_label(
            None, temp_eval_export_dir))
    examples = [
        self._makeExample(
            age=3.0,
            language='english',
            label=0,
            classes=['english', 'chinese', 'other'],
            scores=[0.9, 0.0, 0.0]),
        self._makeExample(
            age=3.0,
            language='chinese',
            label=1,
            classes=['english', 'chinese', 'other'],
            scores=[0.0, 0.99, 0.0]),
        self._makeExample(
            age=4.0,
            language='english',
            label=0,
            classes=['english', 'chinese', 'other'],
            scores=[0.99, 0.0, 0.0]),
        self._makeExample(
            age=5.0,
            language='chinese',
            label=1,
            classes=['english', 'chinese', 'other'],
            scores=[0.0, 0.89, 0.0]),
        self._makeExample(
            age=5.0,
            language='other',
            label=2,
            classes=['english', 'chinese', 'other'],
            scores=[0.0, 0.0, 0.99]),
    ]
    expected_values_dict = {
        metric_keys.EXAMPLE_COUNT: 5.0,
        metric_keys.EXAMPLE_WEIGHT: 20.0,
        metric_keys.tagged_key(metric_keys.AUC, 'english'): 0.99999952,
        metric_keys.tagged_key(metric_keys.AUC, 'chinese'): 0.9999997,
        metric_keys.tagged_key(metric_keys.AUC, 'other'): 0.99999952,
    }
    self._runTest(examples, eval_export_dir, [
        post_export_metrics.example_count(),
        post_export_metrics.example_weight('age'),
        post_export_metrics.auc(tensor_index=0, metric_tag='english'),
        post_export_metrics.auc(tensor_index=1, metric_tag='chinese'),
        post_export_metrics.auc(tensor_index=2, metric_tag='other'),
    ], expected_values_dict)

  def testPostExportMetricsLinearRegressor(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = linear_regressor.simple_linear_regressor(
        None, temp_eval_export_dir)
    examples = [
        self._makeExample(age=3.0, language='english', label=1.0),
        self._makeExample(age=3.0, language='chinese', label=0.0),
        self._makeExample(age=4.0, language='english', label=1.0),
        self._makeExample(age=5.0, language='chinese', label=0.0)
    ]
    expected_values_dict = {
        metric_keys.EXAMPLE_COUNT: 4.0,
        metric_keys.EXAMPLE_WEIGHT: 15.0,
    }
    self._runTest(examples, eval_export_dir, [
        post_export_metrics.example_count(),
        post_export_metrics.example_weight('age')
    ], expected_values_dict)

  def testPostExportMetricsDNNRegressor(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = dnn_regressor.simple_dnn_regressor(
        None, temp_eval_export_dir)
    examples = [
        self._makeExample(age=3.0, language='english', label=1.0),
        self._makeExample(age=3.0, language='chinese', label=0.0),
        self._makeExample(age=4.0, language='english', label=1.0),
        self._makeExample(age=5.0, language='chinese', label=0.0)
    ]
    expected_values_dict = {
        metric_keys.EXAMPLE_COUNT: 4.0,
        metric_keys.EXAMPLE_WEIGHT: 15.0,
    }
    self._runTest(examples, eval_export_dir, [
        post_export_metrics.example_count(),
        post_export_metrics.example_weight('age')
    ], expected_values_dict)

  def testPrecisionRecallAtKUnweighted(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_classifier.simple_fixed_prediction_classifier(
            None, temp_eval_export_dir))
    examples = [
        self._makeExample(
            classes=['a', 'b', 'c'],
            scores=[0.9, 0.8, 0.7],
            labels=['a', 'c'],
            fixed_float=1.0,
            fixed_string=''),
        self._makeExample(
            classes=['a', 'b', 'c'],
            scores=[0.9, 0.2, 0.1],
            labels=['a'],
            fixed_float=2.0,
            fixed_string=''),
        self._makeExample(
            classes=['a', 'b', 'c'],
            scores=[0.1, 0.2, 0.9],
            labels=['a'],
            fixed_float=3.0,
            fixed_string=''),
    ]

    precision_metric = post_export_metrics.precision_at_k([0, 1, 2, 3, 5])
    recall_metric = post_export_metrics.recall_at_k([0, 1, 2, 3, 5])

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)

        self.assertIn(metric_keys.PRECISION_AT_K, value)
        precision_table = value[metric_keys.PRECISION_AT_K]
        cutoffs = precision_table[:, 0].tolist()
        precision = precision_table[:, 1].tolist()
        self.assertEqual(cutoffs, [0, 1, 2, 3, 5])
        self.assertSequenceAlmostEqual(
            precision, [4.0 / 9.0, 2.0 / 3.0, 2.0 / 6.0, 4.0 / 9.0, 4.0 / 9.0])

        self.assertIn(metric_keys.RECALL_AT_K, value)
        recall_table = value[metric_keys.RECALL_AT_K]
        cutoffs = recall_table[:, 0].tolist()
        recall = recall_table[:, 1].tolist()
        self.assertSequenceAlmostEqual(
            recall, [4.0 / 4.0, 2.0 / 4.0, 2.0 / 4.0, 4.0 / 4.0, 4.0 / 4.0])

        # Check serialization too.
        # Note that we can't just make this a dict, since proto maps
        # allow uninitialized key access, i.e. they act like defaultdicts.
        output_metrics = metrics_for_slice_pb2.MetricsForSlice().metrics
        precision_metric.populate_stats_and_pop(value, output_metrics)
        self.assertProtoEquals(
            """
            value_at_cutoffs {
              values {
                cutoff: 0
                value: 0.44444444
                bounded_value {
                  value {
                    value: 0.4444444
                  }
                }
              }
              values {
                cutoff: 1
                value: 0.66666666
                bounded_value {
                  value {
                    value: 0.66666666
                  }
                }
              }
              values {
                cutoff: 2
                value: 0.33333333
               bounded_value {
                  value {
                    value: 0.33333333
                  }
                }
              }
              values {
                cutoff: 3
                value: 0.44444444
                bounded_value {
                  value {
                    value: 0.4444444
                  }
                }
              }
              values {
                cutoff: 5
                value: 0.44444444
                bounded_value {
                  value {
                    value: 0.4444444
                  }
                }
              }
            }
            """, output_metrics[metric_keys.PRECISION_AT_K])
        output_metrics = metrics_for_slice_pb2.MetricsForSlice().metrics
        recall_metric.populate_stats_and_pop(value, output_metrics)
        self.assertProtoEquals(
            """
            value_at_cutoffs {
              values {
                cutoff: 0
                value: 1.0
                bounded_value {
                  value {
                    value: 1.0
                  }
                }
              }
              values {
                cutoff: 1
                value: 0.5
                bounded_value {
                  value {
                    value: 0.5
                  }
                }
              }
              values {
                cutoff: 2
                value: 0.5
                bounded_value {
                  value {
                    value: 0.5
                  }
                }
              }
              values {
                cutoff: 3
                value: 1.0
                bounded_value {
                  value {
                    value: 1.0
                  }
                }
              }
              values {
                cutoff: 5
                value: 1.0
                bounded_value {
                  value {
                    value: 1.0
                  }
                }
              }
            }
            """, output_metrics[metric_keys.RECALL_AT_K])
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [precision_metric, recall_metric],
        custom_metrics_check=check_result)

  def testPrecisionRecallAtKCast(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_classifier_identity_label
        .simple_fixed_prediction_classifier_identity_label(
            None, temp_eval_export_dir))
    examples = [
        self._makeExample(
            classes=['0', '1', '2'],
            scores=[0.9, 0.8, 0.7],
            label=[2],
            language='ignored',
            age=2.0),
        self._makeExample(
            classes=['0', '1', '2'],
            scores=[0.9, 0.2, 0.1],
            label=[0],
            language='ignored',
            age=2.0),
    ]

    precision_metric = post_export_metrics.precision_at_k([0, 1])
    recall_metric = post_export_metrics.recall_at_k([0, 1])

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)

        self.assertIn(metric_keys.PRECISION_AT_K, value)
        precision_table = value[metric_keys.PRECISION_AT_K]
        cutoffs = precision_table[:, 0].tolist()
        precision = precision_table[:, 1].tolist()
        self.assertEqual(cutoffs, [0, 1])
        self.assertSequenceAlmostEqual(precision, [2.0 / 6.0, 1.0 / 2.0])

        self.assertIn(metric_keys.RECALL_AT_K, value)
        recall_table = value[metric_keys.RECALL_AT_K]
        cutoffs = recall_table[:, 0].tolist()
        recall = recall_table[:, 1].tolist()
        self.assertEqual(cutoffs, [0, 1])
        self.assertSequenceAlmostEqual(recall, [2.0 / 2.0, 1.0 / 2.0])
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [precision_metric, recall_metric],
        custom_metrics_check=check_result)

  def testPrecisionRecallAtKUnweightedWithUncertainty(self):
    self.num_bootstrap_samples = 10
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_classifier.simple_fixed_prediction_classifier(
            None, temp_eval_export_dir))
    examples = [
        self._makeExample(
            classes=['a', 'b', 'c'],
            scores=[0.9, 0.8, 0.7],
            labels=['a', 'c'],
            fixed_float=1.0,
            fixed_string=''),
        self._makeExample(
            classes=['a', 'b', 'c'],
            scores=[0.9, 0.2, 0.1],
            labels=['a'],
            fixed_float=2.0,
            fixed_string=''),
        self._makeExample(
            classes=['a', 'b', 'c'],
            scores=[0.1, 0.2, 0.9],
            labels=['a'],
            fixed_float=3.0,
            fixed_string=''),
    ]

    precision_metric = post_export_metrics.precision_at_k([0, 1, 2, 3, 5])
    recall_metric = post_export_metrics.recall_at_k([0, 1, 2, 3, 5])

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)

        self.assertIn(metric_keys.PRECISION_AT_K, value)
        precision_table = value[metric_keys.PRECISION_AT_K]
        cutoffs = precision_table[:, 0].tolist()
        precision = precision_table[:, 1].tolist()
        expected_cutoffs = [0, 1, 2, 3, 5]
        expected_precision = [
            4.0 / 9.0, 2.0 / 3.0, 2.0 / 6.0, 4.0 / 9.0, 4.0 / 9.0
        ]
        self.assertSequenceAlmostEqual([cutoff.value for cutoff in cutoffs],
                                       expected_cutoffs)
        self.assertSequenceAlmostEqual([prec.value for prec in precision],
                                       expected_precision,
                                       delta=0.2)

        self.assertIn(metric_keys.RECALL_AT_K, value)
        recall_table = value[metric_keys.RECALL_AT_K]
        cutoffs = recall_table[:, 0].tolist()
        recall = recall_table[:, 1].tolist()
        expected_cutoffs = [0, 1, 2, 3, 5]
        expected_recall = [
            4.0 / 4.0, 2.0 / 4.0, 2.0 / 4.0, 4.0 / 4.0, 4.0 / 4.0
        ]
        self.assertSequenceAlmostEqual([cutoff.value for cutoff in cutoffs],
                                       expected_cutoffs)
        self.assertSequenceAlmostEqual([rec.value for rec in recall],
                                       expected_recall,
                                       delta=0.2)

        # Check serialization too.
        # Note that we can't just make this a dict, since proto maps
        # allow uninitialized key access, i.e. they act like defaultdicts.
        output_metrics = metrics_for_slice_pb2.MetricsForSlice().metrics
        precision_metric.populate_stats_and_pop(value, output_metrics)
        for v in output_metrics[
            metric_keys.PRECISION_AT_K].value_at_cutoffs.values:
          # Note that we can't check the exact values because of nondeterminism.
          # We'll check that the values are equivalent, and close enough to the
          # expected precision and recall values for the cutoff.
          expected_value = expected_precision[expected_cutoffs.index(v.cutoff)]
          self.assertAlmostEqual(v.value, expected_value, delta=0.2)
          self.assertAlmostEqual(
              v.bounded_value.value.value, expected_value, delta=0.2)
          self.assertAlmostEqual(
              v.bounded_value.upper_bound.value, expected_value, delta=0.4)
          self.assertAlmostEqual(
              v.bounded_value.lower_bound.value, expected_value, delta=0.4)

        output_metrics = metrics_for_slice_pb2.MetricsForSlice().metrics
        recall_metric.populate_stats_and_pop(value, output_metrics)
        for v in output_metrics[
            metric_keys.RECALL_AT_K].value_at_cutoffs.values:
          # Note that we can't check the exact values because of nondeterminism.
          # We'll check that the values are equivalent, and close enough to the
          # expected precision and recall values for the cutoff.
          expected_value = expected_recall[expected_cutoffs.index(v.cutoff)]
          self.assertAlmostEqual(v.value, expected_value, delta=0.2)
          self.assertAlmostEqual(
              v.bounded_value.value.value, expected_value, delta=0.2)
          self.assertAlmostEqual(
              v.bounded_value.upper_bound.value, expected_value, delta=0.4)
          self.assertAlmostEqual(
              v.bounded_value.lower_bound.value, expected_value, delta=0.4)
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [precision_metric, recall_metric],
        custom_metrics_check=check_result)

  def testPrecisionRecallAtKWeighted(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_classifier_extra_fields
        .simple_fixed_prediction_classifier_extra_fields(
            None, temp_eval_export_dir))
    examples = [
        self._makeExample(
            classes=['a', 'b', 'c'],
            scores=[0.9, 0.8, 0.7],
            labels=['a', 'c'],
            fixed_float=1.0,
            fixed_string='',
            fixed_int=0),
        self._makeExample(
            classes=['a', 'b', 'c'],
            scores=[0.9, 0.2, 0.1],
            labels=['a'],
            fixed_float=2.0,
            fixed_string='',
            fixed_int=0),
        self._makeExample(
            classes=['a', 'b', 'c'],
            scores=[0.1, 0.2, 0.9],
            labels=['a'],
            fixed_float=3.0,
            fixed_string='',
            fixed_int=0),
    ]

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertIn(metric_keys.PRECISION_AT_K, value)
        table = value[metric_keys.PRECISION_AT_K]
        cutoffs = table[:, 0].tolist()
        precision = table[:, 1].tolist()
        self.assertEqual(cutoffs, [1, 3])
        self.assertSequenceAlmostEqual(precision, [3.0 / 6.0, 7.0 / 18.0])

        self.assertIn(metric_keys.RECALL_AT_K, value)
        table = value[metric_keys.RECALL_AT_K]
        cutoffs = table[:, 0].tolist()
        recall = table[:, 1].tolist()
        self.assertEqual(cutoffs, [1, 3])
        self.assertSequenceAlmostEqual(recall, [3.0 / 7.0, 7.0 / 7.0])
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [
            post_export_metrics.precision_at_k(
                [1, 3], example_weight_key='fixed_float'),
            post_export_metrics.recall_at_k([1, 3],
                                            example_weight_key='fixed_float')
        ],
        custom_metrics_check=check_result)

  def testPrecisionRecallAtKEmptyCutoffs(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_classifier_extra_fields
        .simple_fixed_prediction_classifier_extra_fields(
            None, temp_eval_export_dir))
    examples = [
        self._makeExample(
            classes=['a', 'b', 'c'],
            scores=[0.9, 0.8, 0.7],
            labels=['a', 'c'],
            fixed_float=1.0,
            fixed_string='',
            fixed_int=0),
    ]

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertIn(metric_keys.PRECISION_AT_K, value)
        table = value[metric_keys.PRECISION_AT_K]
        cutoffs = table[:, 0].tolist()
        precision = table[:, 1].tolist()
        self.assertEqual(cutoffs, [])
        self.assertSequenceAlmostEqual(precision, [])

        self.assertIn(metric_keys.RECALL_AT_K, value)
        table = value[metric_keys.RECALL_AT_K]
        cutoffs = table[:, 0].tolist()
        recall = table[:, 1].tolist()
        self.assertEqual(cutoffs, [])
        self.assertSequenceAlmostEqual(recall, [])
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [
            post_export_metrics.precision_at_k([]),
            post_export_metrics.recall_at_k([])
        ],
        custom_metrics_check=check_result)

  def testCalibrationPlotAndPredictionHistogramUnweighted(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_estimator.simple_fixed_prediction_estimator(
            None, temp_eval_export_dir))
    examples = [
        # For each example, we set label to prediction + 1.
        # These two go in bucket 0: (-inf, 0)
        self._makeExample(prediction=-10.0, label=-9.0),
        self._makeExample(prediction=-9.0, label=-8.0),
        # This goes in bucket 1: [0, 0.00100)
        self._makeExample(prediction=0.00000, label=1.00000),
        # These three go in bucket 1: [0.00100, 0.00110)
        self._makeExample(prediction=0.00100, label=1.00100),
        self._makeExample(prediction=0.00101, label=1.00101),
        self._makeExample(prediction=0.00102, label=1.00102),
        # These two go in bucket 10000: [0.99990, 1.00000)
        self._makeExample(prediction=0.99998, label=1.99998),
        self._makeExample(prediction=0.99999, label=1.99999),
        # These four go in bucket 10001: [1.0000, +inf)
        self._makeExample(prediction=1.0, label=2.0),
        self._makeExample(prediction=8.0, label=9.0),
        self._makeExample(prediction=9.0, label=10.0),
        self._makeExample(prediction=10.0, label=11.0),
    ]

    calibration_plot = (
        post_export_metrics.calibration_plot_and_prediction_histogram())

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertIn(metric_keys.CALIBRATION_PLOT_MATRICES, value)
        buckets = value[metric_keys.CALIBRATION_PLOT_MATRICES]
        self.assertSequenceAlmostEqual(buckets[0], [-19.0, -17.0, 2.0])
        self.assertSequenceAlmostEqual(buckets[1], [0.0, 1.0, 1.0])
        self.assertSequenceAlmostEqual(buckets[11], [0.00303, 3.00303, 3.0])
        self.assertSequenceAlmostEqual(buckets[10000], [1.99997, 3.99997, 2.0])
        self.assertSequenceAlmostEqual(buckets[10001], [28.0, 32.0, 4.0])
        self.assertIn(metric_keys.CALIBRATION_PLOT_BOUNDARIES, value)
        boundaries = value[metric_keys.CALIBRATION_PLOT_BOUNDARIES]
        self.assertAlmostEqual(0.0, boundaries[0])
        self.assertAlmostEqual(0.001, boundaries[10])
        self.assertAlmostEqual(0.005, boundaries[50])
        self.assertAlmostEqual(0.010, boundaries[100])
        self.assertAlmostEqual(0.100, boundaries[1000])
        self.assertAlmostEqual(0.800, boundaries[8000])
        self.assertAlmostEqual(1.000, boundaries[10000])
        plot_data = metrics_for_slice_pb2.PlotsForSlice().plots
        calibration_plot.populate_plots_and_pop(value, plot_data)
        self.assertProtoEquals(
            """lower_threshold_inclusive:1.0
            upper_threshold_exclusive: inf
            num_weighted_examples {
              value: 4.0
            }
            total_weighted_label {
              value: 32.0
            }
            total_weighted_refined_prediction {
              value: 28.0
            }""", plot_data['post_export_metrics'].calibration_histogram_buckets
            .buckets[10001])
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [calibration_plot],
        custom_plots_check=check_result)

  def testCalibrationPlotAndPredictionHistogramUnweightedWithUncertainty(self):
    self.num_bootstrap_samples = 10
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_estimator.simple_fixed_prediction_estimator(
            None, temp_eval_export_dir))
    examples = [
        # For each example, we set label to prediction + 1.
        # These two go in bucket 0: (-inf, 0)
        self._makeExample(prediction=-10.0, label=-9.0),
        self._makeExample(prediction=-9.0, label=-8.0),
        # This goes in bucket 1: [0, 0.00100)
        self._makeExample(prediction=0.00000, label=1.00000),
        # These three go in bucket 1: [0.00100, 0.00110)
        self._makeExample(prediction=0.00100, label=1.00100),
        self._makeExample(prediction=0.00101, label=1.00101),
        self._makeExample(prediction=0.00102, label=1.00102),
        # These two go in bucket 10000: [0.99990, 1.00000)
        self._makeExample(prediction=0.99998, label=1.99998),
        self._makeExample(prediction=0.99999, label=1.99999),
        # These four go in bucket 10001: [1.0000, +inf)
        self._makeExample(prediction=1.0, label=2.0),
        self._makeExample(prediction=8.0, label=9.0),
        self._makeExample(prediction=9.0, label=10.0),
        self._makeExample(prediction=10.0, label=11.0),
    ]

    calibration_plot = (
        post_export_metrics.calibration_plot_and_prediction_histogram())

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertIn(metric_keys.CALIBRATION_PLOT_MATRICES, value)
        buckets = value[metric_keys.CALIBRATION_PLOT_MATRICES]
        self.assertSequenceAlmostEqual([item.value for item in buckets[0]],
                                       [-19.0, -17.0, 2.0],
                                       delta=2)
        self.assertSequenceAlmostEqual([item.value for item in buckets[1]],
                                       [0.0, 1.0, 1.0],
                                       delta=2)
        self.assertSequenceAlmostEqual([item.value for item in buckets[11]],
                                       [0.00303, 3.00303, 3.0],
                                       delta=2)
        self.assertSequenceAlmostEqual([item.value for item in buckets[10000]],
                                       [1.99997, 3.99997, 2.0],
                                       delta=2)
        self.assertSequenceAlmostEqual([item.value for item in buckets[10001]],
                                       [28.0, 32.0, 4.0],
                                       delta=2)
        self.assertIn(metric_keys.CALIBRATION_PLOT_BOUNDARIES, value)
        boundaries = value[metric_keys.CALIBRATION_PLOT_BOUNDARIES]
        self.assertAlmostEqual(0.0, boundaries[0].value)
        self.assertAlmostEqual(0.001, boundaries[10].value)
        self.assertAlmostEqual(0.005, boundaries[50].value)
        self.assertAlmostEqual(0.010, boundaries[100].value)
        self.assertAlmostEqual(0.100, boundaries[1000].value)
        self.assertAlmostEqual(0.800, boundaries[8000].value)
        self.assertAlmostEqual(1.000, boundaries[10000].value)
        plot_data = metrics_for_slice_pb2.PlotsForSlice().plots
        calibration_plot.populate_plots_and_pop(value, plot_data)
        self.assertProtoEquals(
            """lower_threshold_inclusive:1.0
            upper_threshold_exclusive: inf
            num_weighted_examples {
              value: 4.0
            }
            total_weighted_label {
              value: 32.0
            }
            total_weighted_refined_prediction {
              value: 28.0
            }""", plot_data['post_export_metrics'].calibration_histogram_buckets
            .buckets[10001])
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [calibration_plot],
        custom_plots_check=check_result)

  def testCalibrationPlotAndPredictionHistogramWeighted(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_estimator_extra_fields
        .simple_fixed_prediction_estimator_extra_fields(None,
                                                        temp_eval_export_dir))
    examples = [
        # For each example, we set label to prediction + 1.
        self._makeExample(
            prediction=-10.0,
            label=-9.0,
            fixed_float=1.0,
            fixed_string='',
            fixed_int=0),
        self._makeExample(
            prediction=-9.0,
            label=-8.0,
            fixed_float=2.0,
            fixed_string='',
            fixed_int=0),
        self._makeExample(
            prediction=0.0000,
            label=1.0000,
            fixed_float=0.0,
            fixed_string='',
            fixed_int=0),
        self._makeExample(
            prediction=0.00100,
            label=1.00100,
            fixed_float=1.0,
            fixed_string='',
            fixed_int=0),
        self._makeExample(
            prediction=0.00101,
            label=1.00101,
            fixed_float=2.0,
            fixed_string='',
            fixed_int=0),
        self._makeExample(
            prediction=0.00102,
            label=1.00102,
            fixed_float=3.0,
            fixed_string='',
            fixed_int=0),
        self._makeExample(
            prediction=10.0,
            label=11.0,
            fixed_float=7.0,
            fixed_string='',
            fixed_int=0),
    ]

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertIn(metric_keys.CALIBRATION_PLOT_MATRICES, value)
        buckets = value[metric_keys.CALIBRATION_PLOT_MATRICES]
        self.assertSequenceAlmostEqual(buckets[0], [-28.0, -25.0, 3.0])
        self.assertSequenceAlmostEqual(buckets[1], [0.0, 0.0, 0.0])
        self.assertSequenceAlmostEqual(buckets[11], [0.00608, 6.00608, 6.0])
        self.assertSequenceAlmostEqual(buckets[10001], [70.0, 77.0, 7.0])
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [
            post_export_metrics.calibration_plot_and_prediction_histogram(
                example_weight_key='fixed_float')
        ],
        custom_plots_check=check_result)

  def testAucPlotsUnweighted(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_estimator.simple_fixed_prediction_estimator(
            None, temp_eval_export_dir))
    examples = [
        self._makeExample(prediction=0.0000, label=0.0000),
        self._makeExample(prediction=0.0000, label=1.0000),
        self._makeExample(prediction=0.7000, label=1.0000),
        self._makeExample(prediction=0.8000, label=0.0000),
        self._makeExample(prediction=1.0000, label=1.0000),
    ]

    auc_plots = post_export_metrics.auc_plots()

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertIn(metric_keys.AUC_PLOTS_MATRICES, value)
        matrices = value[metric_keys.AUC_PLOTS_MATRICES]
        #            |      | --------- Threshold -----------
        # true label | pred | -1e-6 | 0.0 | 0.7 | 0.8 | 1.0
        #     -      | 0.0  | FP    | TN  | TN  | TN  | TN
        #     +      | 0.0  | TP    | FN  | FN  | FN  | FN
        #     +      | 0.7  | TP    | TP  | FN  | FN  | FN
        #     -      | 0.8  | FP    | FP  | FP  | TN  | TN
        #     +      | 1.0  | TP    | TP  | TP  | TP  | FN
        self.assertSequenceAlmostEqual(matrices[0],
                                       [0, 0, 2, 3, 3.0 / 5.0, 1.0])
        self.assertSequenceAlmostEqual(matrices[1],
                                       [1, 1, 1, 2, 2.0 / 3.0, 2.0 / 3.0])
        self.assertSequenceAlmostEqual(matrices[7001],
                                       [2, 1, 1, 1, 1.0 / 2.0, 1.0 / 3.0])
        self.assertSequenceAlmostEqual(matrices[8001],
                                       [2, 2, 0, 1, 1.0 / 1.0, 1.0 / 3.0])
        self.assertSequenceAlmostEqual(
            matrices[10001], [3, 2, 0, 0, float('nan'), 0.0])
        self.assertIn(metric_keys.AUC_PLOTS_THRESHOLDS, value)
        thresholds = value[metric_keys.AUC_PLOTS_THRESHOLDS]
        self.assertAlmostEqual(0.0, thresholds[1])
        self.assertAlmostEqual(0.001, thresholds[11])
        self.assertAlmostEqual(0.005, thresholds[51])
        self.assertAlmostEqual(0.010, thresholds[101])
        self.assertAlmostEqual(0.100, thresholds[1001])
        self.assertAlmostEqual(0.800, thresholds[8001])
        self.assertAlmostEqual(1.000, thresholds[10001])
        plot_data = metrics_for_slice_pb2.PlotsForSlice().plots
        auc_plots.populate_plots_and_pop(value, plot_data)
        self.assertProtoEquals(
            """threshold: 1.0
            false_negatives: 3.0
            true_negatives: 2.0
            false_positives: 0.0
            true_positives: 0.0
            precision: nan
            recall: 0.0
            bounded_false_negatives {
              value {
                value: 3.0
              }
            }
            bounded_true_negatives {
              value {
                value: 2.0
              }
            }
            bounded_false_positives {
              value {
              }
            }
            bounded_true_positives {
              value {
              }
            }
            bounded_precision {
              value {
                value: nan
              }
            }
            bounded_recall {
              value {
              }
            }""", plot_data['post_export_metrics']
            .confusion_matrix_at_thresholds.matrices[10001])
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples, eval_export_dir, [auc_plots], custom_plots_check=check_result)

  def testAucPlotsWithUncertainty(self):
    self.num_bootstrap_samples = 3
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_estimator.simple_fixed_prediction_estimator(
            None, temp_eval_export_dir))
    examples = [
        self._makeExample(prediction=0.0000, label=0.0000),
        self._makeExample(prediction=0.0000, label=1.0000),
        self._makeExample(prediction=0.7000, label=1.0000),
        self._makeExample(prediction=0.8000, label=0.0000),
        self._makeExample(prediction=1.0000, label=1.0000),
    ]

    auc_plots = post_export_metrics.auc_plots()

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertIn(metric_keys.AUC_PLOTS_MATRICES, value)
        matrices = value[metric_keys.AUC_PLOTS_MATRICES]
        #            |      | --------- Threshold -----------
        # true label | pred | -1e-6 | 0.0 | 0.7 | 0.8 | 1.0
        #     -      | 0.0  | FP    | TN  | TN  | TN  | TN
        #     +      | 0.0  | TP    | FN  | FN  | FN  | FN
        #     +      | 0.7  | TP    | TP  | FN  | FN  | FN
        #     -      | 0.8  | FP    | FP  | FP  | TN  | TN
        #     +      | 1.0  | TP    | TP  | TP  | TP  | FN
        self.assertSequenceAlmostEqual([matrix.value for matrix in matrices[0]],
                                       [0, 0, 2, 3, 3.0 / 5.0, 1.0])
        self.assertSequenceAlmostEqual([matrix.value for matrix in matrices[1]],
                                       [1, 1, 1, 2, 2.0 / 3.0, 2.0 / 3.0])
        self.assertSequenceAlmostEqual(
            [matrix.value for matrix in matrices[7001]],
            [2, 1, 1, 1, 1.0 / 2.0, 1.0 / 3.0])
        self.assertSequenceAlmostEqual(
            [matrix.value for matrix in matrices[8001]],
            [2, 2, 0, 1, 1.0 / 1.0, 1.0 / 3.0])
        self.assertSequenceAlmostEqual(
            [matrix.value for matrix in matrices[10001]],
            [3, 2, 0, 0, float('nan'), 0.0])
        self.assertIn(metric_keys.AUC_PLOTS_THRESHOLDS, value)
        thresholds = value[metric_keys.AUC_PLOTS_THRESHOLDS]
        self.assertAlmostEqual(0.0, thresholds[1].value)
        self.assertAlmostEqual(0.001, thresholds[11].value)
        self.assertAlmostEqual(0.005, thresholds[51].value)
        self.assertAlmostEqual(0.010, thresholds[101].value)
        self.assertAlmostEqual(0.100, thresholds[1001].value)
        self.assertAlmostEqual(0.800, thresholds[8001].value)
        self.assertAlmostEqual(1.000, thresholds[10001].value)
        plot_data = metrics_for_slice_pb2.PlotsForSlice().plots
        auc_plots.populate_plots_and_pop(value, plot_data)
        self.assertProtoEquals(
            """threshold: 1.0
            false_negatives: 3.0
            true_negatives: 2.0
            false_positives: 0.0
            true_positives: 0.0
            precision: nan
            recall: 0.0
            bounded_false_negatives {
              lower_bound {
                value: 3.0
              }
              upper_bound {
                value: 3.0
              }
              value {
                value: 3.0
              }
              methodology: POISSON_BOOTSTRAP
            }
            bounded_true_negatives {
              lower_bound {
                value: 2.0
              }
              upper_bound {
                value: 2.0
              }
              value {
                value: 2.0
              }
              methodology: POISSON_BOOTSTRAP
            }
            bounded_false_positives {
              lower_bound {
              }
              upper_bound {
              }
              value {
              }
              methodology: POISSON_BOOTSTRAP
            }
            bounded_true_positives {
              lower_bound {
              }
              upper_bound {
              }
              value {
              }
              methodology: POISSON_BOOTSTRAP
            }
            bounded_precision {
              lower_bound {
                value: nan
              }
              upper_bound {
                value: nan
              }
              value {
                value: nan
              }
              methodology: POISSON_BOOTSTRAP
            }
            bounded_recall {
              lower_bound {
              }
              upper_bound {
              }
              value {
              }
              methodology: POISSON_BOOTSTRAP
            }""", plot_data['post_export_metrics']
            .confusion_matrix_at_thresholds.matrices[10001])
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples, eval_export_dir, [auc_plots], custom_plots_check=check_result)

  def makeConfusionMatrixExamples(self):
    """Helper to create a set of examples used by multiple tests."""
    #            |      |       --------- Threshold -----------
    # true label | pred | wt   | -1e-6 | 0.0 | 0.7 | 0.8 | 1.0
    #     -      | 0.0  | 1.0  | FP    | TN  | TN  | TN  | TN
    #     +      | 0.0  | 1.0  | TP    | FN  | FN  | FN  | FN
    #     +      | 0.7  | 3.0  | TP    | TP  | FN  | FN  | FN
    #     -      | 0.8  | 2.0  | FP    | FP  | FP  | TN  | TN
    #     +      | 1.0  | 3.0  | TP    | TP  | TP  | TP  | FN
    return [
        self._makeExample(
            prediction=0.0000,
            label=0.0000,
            fixed_float=1.0,
            fixed_string='',
            fixed_int=0),
        self._makeExample(
            prediction=0.0000,
            label=1.0000,
            fixed_float=1.0,
            fixed_string='',
            fixed_int=0),
        self._makeExample(
            prediction=0.7000,
            label=1.0000,
            fixed_float=3.0,
            fixed_string='',
            fixed_int=0),
        self._makeExample(
            prediction=0.8000,
            label=0.0000,
            fixed_float=2.0,
            fixed_string='',
            fixed_int=0),
        self._makeExample(
            prediction=1.0000,
            label=1.0000,
            fixed_float=3.0,
            fixed_string='',
            fixed_int=0),
    ]

  def testConfusionMatrixAtThresholdsWeighted(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_estimator_extra_fields
        .simple_fixed_prediction_estimator_extra_fields(None,
                                                        temp_eval_export_dir))
    examples = self.makeConfusionMatrixExamples()

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertIn(metric_keys.CONFUSION_MATRIX_AT_THRESHOLDS_MATRICES,
                      value)
        matrices = value[metric_keys.CONFUSION_MATRIX_AT_THRESHOLDS_MATRICES]
        self.assertSequenceAlmostEqual(matrices[0],
                                       [0.0, 0.0, 3.0, 7.0, 7.0 / 10.0, 1.0])
        self.assertSequenceAlmostEqual(
            matrices[1], [1.0, 1.0, 2.0, 6.0, 6.0 / 8.0, 6.0 / 7.0])
        self.assertSequenceAlmostEqual(
            matrices[2], [4.0, 1.0, 2.0, 3.0, 3.0 / 5.0, 3.0 / 7.0])
        self.assertSequenceAlmostEqual(matrices[3],
                                       [4.0, 3.0, 0.0, 3.0, 1.0, 3.0 / 7.0])
        self.assertSequenceAlmostEqual(
            matrices[4],
            [7.0, 3.0, 0.0, 0.0, float('nan'), 0.0])
        self.assertIn(metric_keys.CONFUSION_MATRIX_AT_THRESHOLDS_THRESHOLDS,
                      value)
        thresholds = value[
            metric_keys.CONFUSION_MATRIX_AT_THRESHOLDS_THRESHOLDS]
        self.assertAlmostEqual(-1e-6, thresholds[0])
        self.assertAlmostEqual(0.0, thresholds[1])
        self.assertAlmostEqual(0.7, thresholds[2])
        self.assertAlmostEqual(0.8, thresholds[3])
        self.assertAlmostEqual(1.0, thresholds[4])
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [
            post_export_metrics.confusion_matrix_at_thresholds(
                example_weight_key='fixed_float',
                thresholds=[-1e-6, 0.0, 0.7, 0.8, 1.0])
        ],
        custom_metrics_check=check_result)

  def testConfusionMatrixAtThresholdsWeightedUncertainty(self):
    self.num_bootstrap_samples = 10
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_estimator_extra_fields
        .simple_fixed_prediction_estimator_extra_fields(None,
                                                        temp_eval_export_dir))
    examples = self.makeConfusionMatrixExamples()

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertIn(metric_keys.CONFUSION_MATRIX_AT_THRESHOLDS_MATRICES,
                      value)
        matrices = value[metric_keys.CONFUSION_MATRIX_AT_THRESHOLDS_MATRICES]
        self.assertSequenceAlmostEqual([matrix.value for matrix in matrices[0]],
                                       [0.0, 0.0, 3.0, 7.0, 7.0 / 10.0, 1.0])
        self.assertSequenceAlmostEqual(
            [matrix.value for matrix in matrices[1]],
            [1.0, 1.0, 2.0, 6.0, 6.0 / 8.0, 6.0 / 7.0])
        self.assertSequenceAlmostEqual(
            [matrix.value for matrix in matrices[2]],
            [4.0, 1.0, 2.0, 3.0, 3.0 / 5.0, 3.0 / 7.0])
        self.assertSequenceAlmostEqual([matrix.value for matrix in matrices[3]],
                                       [4.0, 3.0, 0.0, 3.0, 1.0, 3.0 / 7.0])
        self.assertSequenceAlmostEqual(
            [matrix.value for matrix in matrices[4]],
            [7.0, 3.0, 0.0, 0.0, float('nan'), 0.0])
        self.assertIn(metric_keys.CONFUSION_MATRIX_AT_THRESHOLDS_THRESHOLDS,
                      value)
        thresholds = value[
            metric_keys.CONFUSION_MATRIX_AT_THRESHOLDS_THRESHOLDS]
        self.assertAlmostEqual(-1e-6, thresholds[0].value)
        self.assertAlmostEqual(0.0, thresholds[1].value)
        self.assertAlmostEqual(0.7, thresholds[2].value, places=5)
        self.assertAlmostEqual(0.8, thresholds[3].value)
        self.assertAlmostEqual(1.0, thresholds[4].value)
        # Test serialization!
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [
            post_export_metrics.confusion_matrix_at_thresholds(
                example_weight_key='fixed_float',
                thresholds=[-1e-6, 0.0, 0.7, 0.8, 1.0])
        ],
        custom_metrics_check=check_result)

  def testConfusionMatrixAtThresholdsSerialization(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_estimator.simple_fixed_prediction_estimator(
            None, temp_eval_export_dir))
    examples = [
        self._makeExample(prediction=0.0000, label=0.0000),
        self._makeExample(prediction=0.5000, label=1.0000),
        self._makeExample(prediction=1.0000, label=1.0000),
    ]

    confusion_matrix_at_thresholds_metric = (
        post_export_metrics.confusion_matrix_at_thresholds(
            thresholds=[0.25, 0.75, 1.00]))

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertIn(metric_keys.CONFUSION_MATRIX_AT_THRESHOLDS_MATRICES,
                      value)
        matrices = value[metric_keys.CONFUSION_MATRIX_AT_THRESHOLDS_MATRICES]
        #            |      | ---- Threshold ----
        # true label | pred | 0.25 | 0.75 | 1.00
        #     -      | 0.0  | TN   | TN   | TN
        #     +      | 0.5  | TP   | FN   | FN
        #     +      | 1.0  | TP   | TP   | FN
        self.assertSequenceAlmostEqual(matrices[0],
                                       [0.0, 1.0, 0.0, 2.0, 1.0, 1.0])
        self.assertSequenceAlmostEqual(matrices[1],
                                       [1.0, 1.0, 0.0, 1.0, 1.0, 0.5])
        self.assertSequenceAlmostEqual(
            matrices[2],
            [2.0, 1.0, 0.0, 0.0, float('nan'), 0.0])
        self.assertIn(metric_keys.CONFUSION_MATRIX_AT_THRESHOLDS_THRESHOLDS,
                      value)
        thresholds = value[
            metric_keys.CONFUSION_MATRIX_AT_THRESHOLDS_THRESHOLDS]
        self.assertAlmostEqual(0.25, thresholds[0])
        self.assertAlmostEqual(0.75, thresholds[1])
        self.assertAlmostEqual(1.00, thresholds[2])

        # Check serialization too.
        # Note that we can't just make this a dict, since proto maps
        # allow uninitialized key access, i.e. they act like defaultdicts.
        output_metrics = metrics_for_slice_pb2.MetricsForSlice().metrics
        confusion_matrix_at_thresholds_metric.populate_stats_and_pop(
            value, output_metrics)
        self.assertProtoEquals(
            """
            confusion_matrix_at_thresholds {
              matrices {
                threshold: 0.25
                false_negatives: 0.0
                true_negatives: 1.0
                false_positives: 0.0
                true_positives: 2.0
                precision: 1.0
                recall: 1.0
                bounded_false_negatives {
                  value {
                    value: 0.0
                  }
                }
                bounded_true_negatives {
                  value {
                    value: 1.0
                  }
                }
                bounded_false_positives {
                  value {
                    value: 0.0
                  }
                }
                bounded_true_positives {
                  value {
                    value: 2.0
                  }
                }
                bounded_precision {
                  value {
                    value: 1.0
                  }
                }
                bounded_recall {
                  value {
                    value: 1.0
                  }
                }
              }
              matrices {
                threshold: 0.75
                false_negatives: 1.0
                true_negatives: 1.0
                false_positives: 0.0
                true_positives: 1.0
                precision: 1.0
                recall: 0.5
                bounded_false_negatives {
                  value {
                    value: 1.0
                  }
                }
                bounded_true_negatives {
                  value {
                    value: 1.0
                  }
                }
                bounded_false_positives {
                  value {
                    value: 0.0
                  }
                }
                bounded_true_positives {
                  value {
                    value: 1.0
                  }
                }
                bounded_precision {
                  value {
                    value: 1.0
                  }
                }
                bounded_recall {
                  value {
                    value: 0.5
                  }
                }
              }
              matrices {
                threshold: 1.00
                false_negatives: 2.0
                true_negatives: 1.0
                false_positives: 0.0
                true_positives: 0.0
                precision: nan
                recall: 0.0
                bounded_false_negatives {
                  value {
                    value: 2.0
                  }
                }
                bounded_true_negatives {
                  value {
                    value: 1.0
                  }
                }
                bounded_false_positives {
                  value {
                    value: 0.0
                  }
                }
                bounded_true_positives {
                  value {
                    value: 0.0
                  }
                }
                bounded_precision {
                  value {
                    value: nan
                  }
                }
                bounded_recall {
                  value {
                    value: 0.0
                  }
                }
              }
            }
            """, output_metrics[metric_keys.CONFUSION_MATRIX_AT_THRESHOLDS])
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [confusion_matrix_at_thresholds_metric],
        custom_metrics_check=check_result)

  def testMetricsWithMultiHead(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        multi_head.simple_multi_head(None, temp_eval_export_dir))

    examples = [
        self._makeExample(
            age=3.0,
            language='english',
            english_label=1.0,
            chinese_label=0.0,
            other_label=0.0),
        self._makeExample(
            age=3.0,
            language='chinese',
            english_label=0.0,
            chinese_label=1.0,
            other_label=0.0),
        self._makeExample(
            age=4.0,
            language='english',
            english_label=1.0,
            chinese_label=0.0,
            other_label=0.0),
        self._makeExample(
            age=5.0,
            language='chinese',
            english_label=0.0,
            chinese_label=1.0,
            other_label=0.0),
        self._makeExample(
            age=6.0,
            language='chinese',
            english_label=0.0,
            chinese_label=1.0,
            other_label=0.0),
    ]

    def check_plot_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertIn(
            metric_keys.tagged_key(metric_keys.CALIBRATION_PLOT_MATRICES,
                                   'chinese_head'), value)
        self.assertIn(
            metric_keys.tagged_key(metric_keys.CALIBRATION_PLOT_MATRICES,
                                   'english_head'), value)
        self.assertIn(
            metric_keys.tagged_key(metric_keys.CALIBRATION_PLOT_MATRICES,
                                   'chinese_head'), value)
        self.assertIn(
            metric_keys.tagged_key(metric_keys.CALIBRATION_PLOT_MATRICES,
                                   'english_head'), value)
        # We just check that the bucket sums look sane, since we don't know
        # the exact predictions of the model.
        buckets = value[metric_keys.tagged_key(
            metric_keys.CALIBRATION_PLOT_MATRICES, 'chinese_head')]
        bucket_sums = np.sum(buckets, axis=0)
        self.assertAlmostEqual(bucket_sums[1], 3.0)  # label sum
        self.assertAlmostEqual(bucket_sums[2], 5.0)  # weight sum
        buckets = value[metric_keys.tagged_key(
            metric_keys.CALIBRATION_PLOT_MATRICES, 'english_head')]
        bucket_sums = np.sum(buckets, axis=0)
        self.assertAlmostEqual(bucket_sums[1], 2.0)  # label sum
        self.assertAlmostEqual(bucket_sums[2], 5.0)  # weight sum
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [
            post_export_metrics.calibration_plot_and_prediction_histogram(
                target_prediction_keys=['chinese_head/logistic'],
                labels_key='chinese_head',
                metric_tag='chinese_head'),
            post_export_metrics.calibration_plot_and_prediction_histogram(
                target_prediction_keys=['english_head/logistic'],
                labels_key='english_head',
                metric_tag='english_head')
        ],
        custom_plots_check=check_plot_result)

  def testCalibrationPlotAndPredictionHistogramLinearClassifier(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        linear_classifier.simple_linear_classifier(None, temp_eval_export_dir))

    examples = [
        self._makeExample(age=3.0, language='english', label=1.0),
        self._makeExample(age=3.0, language='chinese', label=0.0),
        self._makeExample(age=4.0, language='english', label=1.0),
        self._makeExample(age=5.0, language='chinese', label=0.0)
    ]

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertIn(metric_keys.CALIBRATION_PLOT_MATRICES, value)
        # We just check that the bucket sums look sane, since we don't know
        # the exact predictions of the model.
        #
        # Note that the correctness of the bucketing is tested in the other
        # two tests with the fixed prediction estimator. This test is more
        # for ensuring that this metric is compatible with the canned
        # Estimators, for which the prediction Tensor returned for a batch
        # of examples will be a N x 1 Tensor, rather than just an N element
        # vector.
        buckets = value[metric_keys.CALIBRATION_PLOT_MATRICES]
        bucket_sums = np.sum(buckets, axis=0)
        self.assertAlmostEqual(bucket_sums[1], 2.0)  # label sum
        self.assertAlmostEqual(bucket_sums[2], 4.0)  # weight sum
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir,
        [post_export_metrics.calibration_plot_and_prediction_histogram()],
        custom_plots_check=check_result)

  def testCalibrationPlotSerialization(self):
    # Calibration plots for the model
    # {prediction:0.3, true_label:+},
    # {prediction:0.7, true_label:-}
    #
    # These plots were generated by hand. For this test to make sense
    # it must actually match the kind of output the TFMA produces.
    tfma_plots = {
        metric_keys.CALIBRATION_PLOT_MATRICES:
            np.array([
                [0.0, 0.0, 0.0],
                [0.3, 1.0, 1.0],
                [0.7, 0.0, 1.0],
                [0.0, 0.0, 0.0],
            ]),
        metric_keys.CALIBRATION_PLOT_BOUNDARIES:
            np.array([0.0, 0.5, 1.0]),
    }
    expected_plot_data = """
      calibration_histogram_buckets {
        buckets {
          lower_threshold_inclusive: -inf
          upper_threshold_exclusive: 0.0
          num_weighted_examples { value: 0.0 }
          total_weighted_label { value: 0.0 }
          total_weighted_refined_prediction { value: 0.0 }
        }
        buckets {
          lower_threshold_inclusive: 0.0
          upper_threshold_exclusive: 0.5
          num_weighted_examples { value: 1.0 }
          total_weighted_label { value: 1.0 }
          total_weighted_refined_prediction { value: 0.3 }
        }
        buckets {
          lower_threshold_inclusive: 0.5
          upper_threshold_exclusive: 1.0
          num_weighted_examples { value: 1.0 }
          total_weighted_label { value: 0.0 }
          total_weighted_refined_prediction { value: 0.7 }
        }
        buckets {
          lower_threshold_inclusive: 1.0
          upper_threshold_exclusive: inf
          num_weighted_examples { value: 0.0 }
          total_weighted_label { value: 0.0 }
          total_weighted_refined_prediction { value: 0.0 }
        }
      }
    """
    plot_data = metrics_for_slice_pb2.PlotsForSlice().plots
    calibration_plot = (
        post_export_metrics.calibration_plot_and_prediction_histogram())
    calibration_plot.populate_plots_and_pop(tfma_plots, plot_data)
    self.assertProtoEquals(expected_plot_data, plot_data['post_export_metrics'])
    self.assertNotIn(metric_keys.CALIBRATION_PLOT_MATRICES, tfma_plots)
    self.assertNotIn(metric_keys.CALIBRATION_PLOT_BOUNDARIES, tfma_plots)

  def testAucUnweighted(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_estimator.simple_fixed_prediction_estimator(
            None, temp_eval_export_dir))
    examples = [
        self._makeExample(prediction=0.0000, label=0.0000),
        self._makeExample(prediction=0.0000, label=1.0000),
        self._makeExample(prediction=0.7000, label=1.0000),
        self._makeExample(prediction=0.8000, label=0.0000),
        self._makeExample(prediction=1.0000, label=1.0000),
    ]

    expected_values_dict = {
        metric_keys.AUC: 0.58333,
        metric_keys.lower_bound_key(metric_keys.AUC): 0.5,
        metric_keys.upper_bound_key(metric_keys.AUC): 0.66667,
        metric_keys.AUPRC: 0.74075,
        metric_keys.lower_bound_key(metric_keys.AUPRC): 0.70000,
        metric_keys.upper_bound_key(metric_keys.AUPRC): 0.77778,
    }

    self._runTest(
        examples, eval_export_dir,
        [post_export_metrics.auc(),
         post_export_metrics.auc(curve='PR')], expected_values_dict)

  def testAucUnweightedSerialization(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_estimator.simple_fixed_prediction_estimator(
            None, temp_eval_export_dir))
    examples = [
        self._makeExample(prediction=0.0000, label=0.0000),
        self._makeExample(prediction=0.0000, label=1.0000),
        self._makeExample(prediction=0.7000, label=1.0000),
        self._makeExample(prediction=0.8000, label=0.0000),
        self._makeExample(prediction=1.0000, label=1.0000),
    ]

    expected_values_dict = {
        metric_keys.lower_bound_key(metric_keys.AUPRC): 0.74075,
        metric_keys.lower_bound_key(metric_keys.AUPRC): 0.70000,
        metric_keys.upper_bound_key(metric_keys.AUPRC): 0.77778,
    }

    auc_metric = post_export_metrics.auc(curve='PR')

    def check_result(got):  # pylint: disable=invalid-name
      try:
        self.assertEqual(1, len(got), 'got: %s' % got)
        (slice_key, value) = got[0]
        self.assertEqual((), slice_key)
        self.assertDictElementsAlmostEqual(value, expected_values_dict)

        # Check serialization too.
        # Note that we can't just make this a dict, since proto maps
        # allow uninitialized key access, i.e. they act like defaultdicts.
        output_metrics = metrics_for_slice_pb2.MetricsForSlice().metrics
        auc_metric.populate_stats_and_pop(value, output_metrics)
        self.assertProtoEquals(
            """
            bounded_value {
              lower_bound {
                value: 0.6999999
              }
              upper_bound {
                value: 0.7777776
              }
              value {
                value: 0.7407472
              }
              methodology: RIEMANN_SUM
            }
            """, output_metrics[metric_keys.AUPRC])
      except AssertionError as err:
        raise util.BeamAssertException(err)

    self._runTestWithCustomCheck(
        examples,
        eval_export_dir, [auc_metric],
        custom_metrics_check=check_result)

  def testAucUnweightedFractionalLabels(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_estimator.simple_fixed_prediction_estimator(
            None, temp_eval_export_dir))
    examples = [
        self._makeExample(prediction=0.0000, label=0.0000),
        self._makeExample(prediction=0.7000, label=0.6000),
        self._makeExample(prediction=1.0000, label=0.8000),
    ]

    # This expands out to:
    #
    # prediction | label | weight
    #     0.0    |   -   |  1.0
    #     0.7    |   -   |  0.4
    #     0.7    |   +   |  0.6
    #     1.0    |   -   |  0.2
    #     1.0    |   +   |  0.8
    #
    # The AUC is (0.8 / 1.4 * (1.0 + 0.4 + 0.2 * 0.5) / 1.6) +
    #            (0.6 / 1.4 * (1.0 + 0.4 * 0.5) / 1.6)
    #          = 0.857143
    #
    # threshold |  TP  |  FP  | precision | recall
    # all +     |  1.4 |  1.6 | 0.46666   | 1.0
    # >0.0      |  1.4 |  0.6 | 0.7       | 1.0
    # >0.7      |  0.8 |  0.2 | 0.8       | 0.571428
    # all -     |  0.0 |  0.0 | N/A       | 0.0
    #
    # Using the trapezoidial estimate, we compute the AUPRC as follows:
    # AUPRC = 0.8(0.571428) + 0.5(0.571428)(0.2) +
    #         0.7(1-0.571428) + 0.5(1-0.571428)(0.1) = 0.8357143
    #
    # However, note that we are now using the 'careful_interpolation' estimate,
    # which gives a different estimate.

    expected_values_dict = {
        metric_keys.AUC:
            0.8571425,
        metric_keys.lower_bound_key(metric_keys.AUC):
            0.7678569,
        metric_keys.upper_bound_key(metric_keys.AUC):
            0.94642806,
        # Note that 'trapeozidal' produces an AUPRC of 0.8357143, which
        # agrees with the old Lantern, but we are now using
        # 'careful_interpolation', which gives this estimate instead.
        metric_keys.AUPRC:
            0.773698,
        metric_keys.lower_bound_key(metric_keys.AUPRC):
            0.75714254,
        metric_keys.upper_bound_key(metric_keys.AUPRC):
            0.91428518,
    }

    self._runTest(
        examples, eval_export_dir,
        [post_export_metrics.auc(),
         post_export_metrics.auc(curve='PR')], expected_values_dict)

  def testAucWeightedFractionalLabels(self):
    temp_eval_export_dir = self._getEvalExportDir()
    _, eval_export_dir = (
        fixed_prediction_estimator_extra_fields
        .simple_fixed_prediction_estimator_extra_fields(None,
                                                        temp_eval_export_dir))

    # Same set of examples as in the unweighted case, except this time
    # with weights.
    examples = [
        self._makeExample(
            prediction=0.0000,
            label=0.0000,
            fixed_float=1.0,
            fixed_string='',
            fixed_int=5),
        self._makeExample(
            prediction=0.7000,
            label=0.6000,
            fixed_float=0.5,
            fixed_string='',
            fixed_int=5),
        self._makeExample(
            prediction=1.0000,
            label=0.8000,
            fixed_float=2.0,
            fixed_string='',
            fixed_int=5),
    ]

    # This expands out to:
    #
    # prediction | label | weight
    #     0.0    |   -   |  1.0
    #     0.7    |   -   |  0.2
    #     0.7    |   +   |  0.3
    #     1.0    |   -   |  0.4
    #     1.0    |   +   |  1.6
    #
    # The AUC is (1.6 / 1.9 * 1.4 / 1.6) + (0.3 / 1.9 * 1.1 / 1.6) = 0.8453947

    expected_values_dict = {
        metric_keys.AUC: 0.8453947,
        metric_keys.lower_bound_key(metric_keys.AUC): 0.73026288,
        metric_keys.upper_bound_key(metric_keys.AUC): 0.96052581,
        metric_keys.AUPRC: 0.79660767,
        metric_keys.lower_bound_key(metric_keys.AUPRC): 0.79368389,
        metric_keys.upper_bound_key(metric_keys.AUPRC): 0.96842057,
    }

    self._runTest(examples, eval_export_dir, [
        post_export_metrics.auc(example_weight_key='fixed_float'),
        post_export_metrics.auc(curve='PR', example_weight_key='fixed_float')
    ], expected_values_dict)

  def testAucPlotSerialization(self):
    # Auc for the model
    # {prediction:0.3, true_label:+},
    # {prediction:0.7, true_label:-}
    #
    # These plots were generated by hand. For this test to make sense
    # it must actually match the kind of output the TFMA produces.
    tfma_plots = {
        metric_keys.AUC_PLOTS_MATRICES:
            np.array([
                [0.0, 0.0, 1.0, 1.0, 0.5, 1.0],
                [0.0, 0.0, 1.0, 1.0, 0.5, 1.0],
                [1.0, 0.0, 1.0, 0.0, 0.0, 0.0],
                [1.0, 1.0, 0.0, 0.0, 0.0, 0.0],
            ]),
        metric_keys.AUC_PLOTS_THRESHOLDS:
            np.array([1e-6, 0, 0.5, 1.0]),
    }
    expected_plot_data = """
      confusion_matrix_at_thresholds {
        matrices {
          threshold: 1e-6
          true_positives: 1.0
          false_positives: 1.0
          true_negatives: 0.0
          false_negatives: 0.0
          precision: 0.5
          recall: 1.0
          bounded_false_negatives {
            value {
            }
          }
          bounded_true_negatives {
            value {
            }
          }
          bounded_false_positives {
            value {
              value: 1.0
            }
          }
          bounded_true_positives {
            value {
              value: 1.0
            }
          }
          bounded_precision {
            value {
              value: 0.5
            }
          }
          bounded_recall {
            value {
              value: 1.0
            }
          }
        }
      }
      confusion_matrix_at_thresholds {
        matrices {
          threshold: 0
          true_positives: 1.0
          false_positives: 1.0
          true_negatives: 0.0
          false_negatives: 0.0
          precision: 0.5
          recall: 1.0
          bounded_false_negatives {
            value {
            }
          }
          bounded_true_negatives {
            value {
            }
          }
          bounded_false_positives {
            value {
              value: 1.0
            }
          }
          bounded_true_positives {
            value {
              value: 1.0
            }
          }
          bounded_precision {
            value {
              value: 0.5
            }
          }
          bounded_recall {
            value {
              value: 1.0
            }
          }
        }
      }
      confusion_matrix_at_thresholds {
        matrices {
          threshold: 0.5
          true_positives: 0.0
          false_positives: 1.0
          true_negatives: 0.0
          false_negatives: 1.0
          precision: 0.0
          recall: 0.0
          bounded_false_negatives {
            value {
              value: 1.0
            }
          }
          bounded_true_negatives {
            value {
            }
          }
          bounded_false_positives {
            value {
              value: 1.0
            }
          }
          bounded_true_positives {
            value {
            }
          }
          bounded_precision {
            value {
            }
          }
          bounded_recall {
            value {
            }
          }
        }
      }
      confusion_matrix_at_thresholds {
        matrices {
          threshold: 1.0
          true_positives: 0.0
          false_positives: 0.0
          true_negatives: 1.0
          false_negatives: 1.0
          precision: 0.0
          recall: 0.0
          bounded_false_negatives {
            value {
              value: 1.0
            }
          }
          bounded_true_negatives {
            value {
              value: 1.0
            }
          }
          bounded_false_positives {
            value {
            }
          }
          bounded_true_positives {
            value {
            }
          }
          bounded_precision {
            value {
            }
          }
          bounded_recall {
            value {
            }
          }
       }
     }
    """
    plot_data = metrics_for_slice_pb2.PlotsForSlice().plots
    auc_plots = post_export_metrics.auc_plots()
    auc_plots.populate_plots_and_pop(tfma_plots, plot_data)
    self.assertProtoEquals(expected_plot_data, plot_data['post_export_metrics'])
    self.assertNotIn(metric_keys.AUC_PLOTS_MATRICES, tfma_plots)
    self.assertNotIn(metric_keys.AUC_PLOTS_THRESHOLDS, tfma_plots)


if __name__ == '__main__':
  tf.test.main()
