import os

import numpy      as np
import tensorflow as tf

from random import shuffle


def load_dataset(model, num_gpus, output_dims, input_dims, seq_length, size, base_data_path, dataset, istraining):
    """
    Function load dataset, setup queue and read data
    Args:
        :model:              tf-activity-recognition framework model object
        :input_dims:         Number of frames used in input
        :output_dims:        Integer number of classes in current dataset
        :seq_length:         Length of output sequence expected from LSTM
        :size:               List detailing height and width of frame
        :num_gpus:           Number of gpus to use when training
        :dataset:            Name of dataset being processed
        :base_data_path:     Full path to root directory containing datasets
        :istraining:         Boolean variable indicating training/testing phase 

    Return:
        Input data tensor, label tensor and names of loaded data (video/image)
    """

    # Get a list of tfrecords file names from which to pull videos
    filenames           = []
    number_of_tfrecords = 0

    for f in os.listdir(base_data_path):
        filenames.append(os.path.join(base_data_path,f))
        number_of_tfrecords += 1

    # END FOR

    print "Number of records available: ", number_of_tfrecords

    # Create Queue which will read in videos num_gpus at a time (Queue seeded for repeatability of experiments)
    tfrecord_file_queue = tf.train.string_input_producer(filenames, shuffle=istraining, name='q', seed=0)

    tf.set_random_seed(0) # To ensure the numbers are generated for temporal offset consistently

    input_data_list     = []
    labels_list 	= []
    names_list 		= []

    # Read in num_gpus number of videos from queue
    for gpu_idx in range(num_gpus):

        # Dequeue video data from queue and convert it from TFRecord format (int64 or bytes)
        features = _read_tfrecords(tfrecord_file_queue)
        frames   = tf.cast(features['Frames'], tf.int32)
        height   = tf.cast(features['Height'], tf.int32)
        width    = tf.cast(features['Width'], tf.int32)
        channel  = tf.cast(features['Channels'], tf.int32)
        label    = tf.cast(features['Label'], tf.int32)

        name     = features['Name']

        input_data_tensor = tf.reshape(tf.decode_raw(features['Data'], tf.uint8), tf.stack([frames,height,width,channel]))

        # BGR to RGB
        input_data_tensor = input_data_tensor[...,::-1]

        # Reduction in fps to 25 for HMDB51 dataset
        if 'HMDB51' in dataset:
            input_data_tensor, frames, indices = _reduce_fps(input_data_tensor, frames)

        # Call preprocessing function related to model chosen
        input_data_tensor, labels_tensor = model.preprocess_tfrecords(input_data_tensor, frames, height, width, channel, input_dims, output_dims, seq_length, size, label, istraining)

        input_data_list.append(input_data_tensor)
        labels_list.append(labels_tensor)
        names_list.append(name)

    input_data_tensor = tf.convert_to_tensor(input_data_list)
    labels_tensor     = tf.convert_to_tensor(labels_list)
    names             = tf.convert_to_tensor(names_list)

    return input_data_tensor, labels_tensor, names


def _read_tfrecords(filename_queue):
    """
    Function that reads and returns the tfrecords of a selected dataset 
    Args:
        :model:              tf-activity-recognition framework model object
        :input_dims:         Number of frames used in input
        :output_dims:        Integer number of classes in current dataset
        :seq_length:         Length of output sequence expected from LSTM
        :size:               List detailing height and width of frame
        :num_gpus:           Number of gpus to use when training
        :dataset:            Name of dataset being processed
        :base_data_path:     Full path to root directory containing datasets
        :istraining:         Boolean variable indicating training/testing phase 

    Return:
        Dictionary containing features of a single sample 
    """
    feature_dict = {}
    reader       = tf.TFRecordReader()

    _, serialized_example = reader.read(filename_queue)


    feature_dict['Label']    = tf.FixedLenFeature([], tf.int64)
    feature_dict['Data']     = tf.FixedLenFeature([], tf.string)
    feature_dict['Frames']   = tf.FixedLenFeature([], tf.int64)
    feature_dict['Height']   = tf.FixedLenFeature([], tf.int64)
    feature_dict['Width']    = tf.FixedLenFeature([], tf.int64)
    feature_dict['Channels'] = tf.FixedLenFeature([], tf.int64)
    feature_dict['Name']     = tf.FixedLenFeature([], tf.string)

    features = tf.parse_single_example(serialized_example, features=feature_dict)

    return features

def _reduce_fps(video, frame_count):
    """
    Function that drops frames to match 25 pfs from 30 fps captured videos 
    Args:
        :video:       Tensor containing video frames 
        :frame_count: Total number of frames in the video

    Return:
        Video with reduced number of frames to match 25fps 
    """
    # Convert from 30 fps to 25 fps
    remove_count = tf.cast(tf.ceil(tf.divide(frame_count, 6)), tf.int32)

    intermediate_frames = tf.multiply(remove_count, 5)
    indices = tf.tile([0,1,2,3,4], [remove_count])                                 # [[0,1,2,3,4],[0,1,2,3,4]..]
    indices = tf.reshape(indices, [intermediate_frames])                           # [0,1,2,3,4,0,1,2,3,4,0,1,2....]
    additions = tf.range(remove_count)                                             # [0,1,2,3,4,5,6,....]
    additions = tf.stack([additions, additions, additions, additions, additions])  # [[0,1,2,3,4,5,6...], [0,1,2,3,4,5,6..], [0,1..], [0,1,..], [0,1,...]]
    additions = tf.transpose(additions)                                            # [[0,0,0,0,0], [1,1,1,1,1], [2,2,2,2,2], ...]
    additions = tf.reshape(additions, [intermediate_frames])                       # [0,0,0,0,0,1,1,1,1,1,2,2,2,2,2,3,3,3,3,3....]
    additions = tf.multiply(additions, 6)                                          # [0,0,0,0,0,6,6,6,6,6,12,12,12,12,12,18,18,18,18,18....]
    indices = tf.add(indices, additions)                                           # [0,1,2,3,4,6,7,8,9,10,12,13,14,15,16,18,19....]

    remove_count = tf.cond( tf.equal(frame_count, tf.multiply(remove_count, 6)),
                    lambda: remove_count,
                    lambda: tf.subtract(remove_count, 1))
    output_frames = tf.subtract(frame_count, remove_count)

    indices = tf.slice(indices, [0], [output_frames])
    indices_to_keep = tf.reshape(indices, [output_frames])
    output = tf.gather(video, indices_to_keep)
    return output, output_frames, indices
