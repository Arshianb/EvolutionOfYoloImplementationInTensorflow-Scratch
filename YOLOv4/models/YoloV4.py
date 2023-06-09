import io
import os

from collections import defaultdict
import tensorflow as tf
import numpy as np
from keras import backend as K
from keras.layers import Conv2D, Input, BatchNormalization, LeakyReLU, ZeroPadding2D, UpSampling2D, MaxPool2D, Activation
from keras.layers import add, concatenate
from keras.models import Model
from keras.utils import get_custom_objects
from tensorflow.python.platform import build_info as tf_build_info

# print("TensorFlow version: {}".format(tf.__version__))
# print("Eager execution: {}".format(tf.executing_eagerly()))
# print("Keras version: {}".format(tf.keras.__version__))
# print("Cuda version: {}".format(tf_build_info.cuda_version_number))
# print("Cudnn version: {}".format(tf_build_info.cudnn_version_number))
# print("Num Physical GPUs Available: ", len(tf.config.experimental.list_physical_devices('GPU')))
# print("Num Logical GPUs Available: ", len(tf.config.experimental.list_logical_devices('GPU')))

NETWORK_W = 608
NETWORK_H = 608

# ------------------------------------------------------------
# needs to be defined as activation class otherwise error
# AttributeError: 'Activation' object has no attribute '__name__'    
class Mish(Activation):
    
    def __init__(self, activation, **kwargs):
        super(Mish, self).__init__(activation, **kwargs)
        self.__name__ = 'mish'


def mysoftplus(x):

    mask_min = tf.cast((x<-20.0),tf.float32)
    ymin = mask_min*tf.math.exp(x)

    mask_max = tf.cast((x>20.0),tf.float32)
    ymax = mask_max*x
    
    mask= tf.cast((abs(x)<=20.0),tf.float32)
    y = mask*tf.math.log(tf.math.exp(x) + 1.0)
    
    return(ymin+ymax+y)    
        


def mish(x):
    return (x* tf.math.tanh(mysoftplus(x)))
    
   
    

get_custom_objects().update({'mish': Mish(mish)})

def _conv_block(inp, convs, skip=False):
    x = inp
    count = 0
    
    for conv in convs:
        if count == (len(convs) - 2) and skip: # handle Shorcut -3 
            skip_connection = x
        count += 1
        
        if conv['stride'] > 1: x = ZeroPadding2D(((1,0),(1,0)), name='zerop_' + str(conv['layer_idx']))(x)  # peculiar padding as darknet prefer left and top
        
        x = Conv2D(conv['filter'], 
                   conv['kernel'], 
                   strides=conv['stride'], 
                   padding='valid' if conv['stride'] > 1 else 'same', # peculiar padding as darknet prefer left and top
                   name='convn_' + str(conv['layer_idx']) if conv['bnorm'] else 'conv_' + str(conv['layer_idx']),
                   use_bias=True)(x)
        
        if conv['bnorm']: x = BatchNormalization(name='BN_' + str(conv['layer_idx']))(x)    
        
        if conv['activ'] == 1: x = LeakyReLU(alpha=0.1, name='leaky_' + str(conv['layer_idx']))(x)
        if conv['activ'] == 2: x = Activation('mish', name='mish_' + str(conv['layer_idx']))(x) 
            
    return add([skip_connection, x],  name='add_' + str(conv['layer_idx']+1)) if skip else x

def make_yolov4_model():
        
    input_image = Input(shape=(NETWORK_H, NETWORK_W, 3), name='input_0')

    # ---------------------------------- begin BACKBONE CSP-Darnet 53 -----------------------------------------------------------
    ## Layer 0 [convolutional]
    x = _conv_block(input_image, [{'filter': 32, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 0}])

    ## Layer 1 [convolutional]
    x = _conv_block(x, [{'filter': 64, 'kernel': 3, 'stride': 2, 'bnorm': True, 'activ': 2, 'layer_idx': 1}])
    layer_1 = x
   
    # ---------- begin 1*Conv+conv+residual 304*304    
    ## Layer 2 [convolutional] 
    x = _conv_block(x, [{'filter':  64, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 2}])
    layer_2 = x
    

    ## Layer 3 [route] layers = -2
    x = layer_1
    ## Layer  4 => 6 [convolutional] + Layer 7 [shortcut]
    x = _conv_block(x, [{'filter': 64, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 4},                  
                        {'filter': 32, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 5},
                        {'filter': 64, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 6}],
                   skip = True) 
    
    ## Layer 8 [convolutional]
    x = _conv_block(x, [{'filter':  64, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 8}])
    layer_8 = x
    
    ## Layer 9 [route] layers = -1, -7
    x = concatenate([layer_8, layer_2], name='concat_9')
    
    ##  Layer 10 => 11 [convolutional]
    x = _conv_block(x, [{'filter':  64, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 10},
                        {'filter': 128, 'kernel': 3, 'stride': 2, 'bnorm': True, 'activ': 2, 'layer_idx': 11}])
    layer_11 = x
    # ---------- end 1*Conv+conv+residual 304*304
   
    # ---------- begin 2*Conv+conv+residual 152*152      
    # Layer 12 [convolutional]
    x = _conv_block(x, [{'filter':  64, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 12}])
    layer_12 = x
    
  
    ## Layer 13 [route] layers = -2
    x = layer_11
    
    ## Layer 14 => 16 [convolutional] + Layer 17 [shortcut]
    x = _conv_block(x, [{'filter':  64, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 14},                    
                        {'filter':  64, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 15},
                        {'filter':  64, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 16}],
                   skip = True)
    
    ## Layer 18 => 19 [convolutional] 
    x = _conv_block(x, [{'filter':  64, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 18},
                        {'filter':  64, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 19}],
                   skip = True)
    
    ## Layer 21 [convolutional] 
    x = _conv_block(x, [{'filter':  64, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 21}]) 
    layer_21 = x
    
    ## Layer 22 [route] layers = -1,-10
    x = concatenate([layer_21, layer_12], name='concat_22')
    
    ## Layer 23 => 24 [convolutional] 
    x = _conv_block(x, [{'filter':  128, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 23},
                        {'filter':  256, 'kernel': 3, 'stride': 2, 'bnorm': True, 'activ': 2, 'layer_idx': 24}])
    layer_24 = x
    # ---------- end 2*Conv+conv+residual 152*152        
    
    # ---------- begin 8*Conv+conv+residual 76*76    
    ## Layer  25 [convolutional] 
    x = _conv_block(x, [{'filter':  128, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 25}])
    layer_25 = x
        
    
    ## Layer 26 [route] layers = -2
    x = layer_24
    
    ## Layer 27 => 29 [convolutional] + Layer 30 [shortcut]
    x = _conv_block(x, [{'filter':  128, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 27},                       
                        {'filter':  128, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 28},
                        {'filter':  128, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 29}],
                   skip = True)
    
    
    ## Layer 31 => 50 [convolutional] and [shortcut] for layers 33, 36, 39, 42, 45, 48, 51
    for i in range(7):
        x = _conv_block(x, [{'filter': 128, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 31+(i*3)},
                            {'filter': 128, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 32+(i*3)}],
                       skip = True)
  
    ## Layer 52 [convolutional]
    x = _conv_block(x, [{'filter':  128, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 52}])
    layer_52 = x
        
    ## Layer 53 [route] layers = -1,-28
    x = concatenate([layer_52, layer_25],  name='concat_53')
    
 
    ## Layer 54 [convolutional]
    x = _conv_block(x, [{'filter':  256, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 54}])
    # ---------- initial output 76*76 
    layer_54 = x
    
    ## Layer 55 [convolutional]
    x = _conv_block(x, [{'filter':  512, 'kernel': 3, 'stride': 2, 'bnorm': True, 'activ': 2, 'layer_idx': 55}])
    layer_55 = x
    # ---------- end 8*Conv+conv+residual 76*76        
    
    # ---------- begin 8*Conv+conv+residual 38*38  
    ## Layer 56 [convolutional]
    x = _conv_block(x, [{'filter':  256, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 56}])
    layer_56 = x
    
    ## Layer 57 [route] layers = -2
    x = layer_55
    
    ##  Layer 58 => 60 [convolutional] + Layer 61 [shortcut]
    x = _conv_block(x, [{'filter':  256, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 58},
                        {'filter':  256, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 59},
                        {'filter':  256, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 60}],
                   skip = True)     
    
    ## Layer 62 => 81 [convolutional] and [shortcut] for layers 64, 67, 70, 73, 76, 79, 82
    for i in range(7):
        x = _conv_block(x, [{'filter': 256, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 62+(i*3)},
                            {'filter': 256, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 63+(i*3)}],
                       skip = True)

    ## Layer 83 [convolutional]
    x = _conv_block(x, [{'filter':  256, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 83}])
    layer_83 = x

    ## Layer 84 [route] layers = -1,-28
    x = concatenate([layer_83, layer_56], name='concat_84')
    
    ## Layer 85 [convolutional]
    x = _conv_block(x, [{'filter':   512, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 85}])
    layer_85 = x
    # ---------- initial output 38*38 
   
    
    ## Layer 86 [convolutional]
    x = _conv_block(x, [{'filter':  1024, 'kernel': 3, 'stride': 2, 'bnorm': True, 'activ': 2, 'layer_idx': 86}])
    layer_86 = x
    
    ## Layer 87 [convolutional]
    x = _conv_block(x, [{'filter':  512, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 87}])
    layer_87 = x
        
    ## Layer 88 [route] layers = -2
    x = layer_86
    # ---------- end 8*Conv+conv+residual 38*38  
    
    # ---------- begin 4*Conv+conv+residual 19*19 
    ## Layer 89 => 91 [convolutional] + Layer 92 [shortcut]
    x = _conv_block(x, [{'filter':  512, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 89},                         
                        {'filter':  512, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 90},
                        {'filter':  512, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 91}],
                   skip = True) 
    
    
    ## Layer 93 => 100 [convolutional] and [shortcut] for layers 95, 98, 101
    for i in range(3):
        x = _conv_block(x, [{'filter': 512, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 93+(i*3)},
                            {'filter': 512, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 94+(i*3)}],
                       skip = True)  
    
    # ---------- end 4*Conv+conv+residual 19*19 
    
    ## Layer 102 [convolutional]
    x = _conv_block(x, [{'filter':  512, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 102}])  
    layer_102 = x
    
    ##  Layer 103 [route] layers = -1,-16
    x = concatenate([layer_102, layer_87], name='concat_103')
    
    ## Layer 104 => 107 [convolutional]
    x = _conv_block(x, [{'filter':  1024, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 2, 'layer_idx': 104},
                        {'filter':   512, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 105},
                        {'filter':  1024, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 106},                        
                        {'filter':   512, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 107}])
    layer_107 = x 
    # ---------------------------------- end BACKBONE CSP-Darnet 53 -----------------------------------------------------------
    
    # ---------------------------------- begin SPP part (Spatial Pyramid Pooling layer) ---------------------------------------
    ## Layer 108 [maxpool]
    x =MaxPool2D(pool_size=(5, 5), strides=1, padding='same', name = 'layer_108')(x)  
    layer_108 = x
    
    ##  Layer 109 [route] layers = -2
    x = layer_107
    
    ## Layer 110 [maxpool]
    x =MaxPool2D(pool_size=(9, 9), strides=1, padding='same', name = 'layer_110')(x)    
    layer_110 = x
    
    ##  Layer 111 [route] layers = -4
    x = layer_107
        
    ## Layer 112 [maxpool]
    x =MaxPool2D(pool_size=(13, 13), strides=1, padding='same', name = 'layer_112')(x) 
    layer_112 = x
    
    ##  Layer 133 [route] layers=-1,-3,-5,-6
    x = concatenate([layer_112, layer_110, layer_108, layer_107], name='concat_113')
    # ---------------------------------- end SPP part (Spatial Pyramid Pooling layer) ---------------------------------------
 

    
    ## Layer 114 => 116 [convolutional]
    x = _conv_block(x, [{'filter':   512, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 114},
                        {'filter':  1024, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 115},
                        {'filter':   512, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 116}])
    layer_116 = x
                        
    ## Layer 117 [convolutional]                    
    x = _conv_block(x, [{'filter':   256, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 117}])
    layer_117 = x
    
    ## Layer 118 [upsample]
    x = UpSampling2D(size=(2, 2), name = 'upsamp_118')(x)
    layer_118 = x
                        
    ##  Layer 119 [route] layers = 85
    x = layer_85
    
    ## Layer 120 [convolutional]
    x = _conv_block(x, [{'filter':  256, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 120}])
    layer_120 = x
                        
    ##  Layer 121 [route] layers = -1, -3
    x = concatenate([layer_120, layer_118],  name='concat_121')
    layer_121 = x    
    
  
    ## Layer 122 => 126 [convolutional]
    x = _conv_block(x, [{'filter':  256, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 122},
                        {'filter':  512, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 123},
                        {'filter':  256, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 124},  
                        {'filter':  512, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 125},
                        {'filter':  256, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 126}])
    layer_126 = x 
    
    
    ## Layer 127 [convolutional]                   
    x = _conv_block(x, [{'filter':  128, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 127}])
    layer_127 = x
    
    ## Layer 128 [upsample]
    x = UpSampling2D(size=(2, 2), name = 'upsamp_128')(x)
    layer_128 = x
    
    # ---------------------------------- begin head output 76*76 ---------------------------------------                   
    ## Layer 129 [route] layers = 54
    x = layer_54
    
    ## Layer 130 [convolutional]
    x = _conv_block(x, [{'filter':  128, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ': 1, 'layer_idx': 130}])
    layer_130 = x
                        
    ## Layer 131 [route] layers = -1, -3                 
    x = concatenate([layer_130, layer_128],  name='concat_131')
    layer_131 = x
    
    # -- begin Convulationnal set 76*76
    ## Layer 132 => 136 [convolutional]
    x = _conv_block(x, [{'filter':  128, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ':  1, 'layer_idx': 132},
                        {'filter':  256, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ':  1, 'layer_idx': 133},
                        {'filter':  128, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ':  1, 'layer_idx': 134},  
                        {'filter':  256, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ':  1, 'layer_idx': 135},
                        {'filter':  128, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ':  1, 'layer_idx': 136}])
    layer_136 = x                   
    
    # -- end Convulationnal set 76*76
    
    # -- beging last Convulationnal 3*3 and 1*1 for 76*76
    ## Layer 137 [convolutional]
    x = _conv_block(x, [{'filter':  256, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ':  1, 'layer_idx': 137}]) 
    layer_137 = x 
    
    ## Layer 138 [convolutional]
    x = _conv_block(x, [{'filter':  255, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ':  0, 'layer_idx': 138}])   
    
    # -- end last Convulationnal 3*3 and 1*1 for 76*76
    
    # -- output 76*76 
    ## Layer 139 [yolo]
    yolo_139 = x
    
    # ---------------------------------- end head output 76*76 ---------------------------------------
    
    # ---------------------------------- begin head output 38*38 ---------------------------------------
    ## Layer 140 [route] layers = -4
    x = layer_136
    
    ##  Layer 141 [convolutional]
    x = _conv_block(x, [{'filter':  256, 'kernel': 3, 'stride': 2, 'bnorm': True, 'activ': 1, 'layer_idx': 141}])
    layer_141 = x
                        
    ## Layer 142 [route] layers = -1, -16                   
    x = concatenate([layer_141, layer_126],  name='concat_142')
    
    # -- begin Convulationnal set 38*38
    ## Layer 143 => 147 [convolutional]
    x = _conv_block(x, [{'filter':  256, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ':   1, 'layer_idx': 143},
                        {'filter':  512, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ':   1, 'layer_idx': 144},
                        {'filter':  256, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ':   1, 'layer_idx': 145},  
                        {'filter':  512, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ':   1, 'layer_idx': 146},
                        {'filter':  256, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ':   1, 'layer_idx': 147}])  
    layer_147 = x
    
    # -- end Convulationnal set 38*38
    
    # -- beging last Convulationnal 3*3 and 1*1 for 38*38
    ## Layer 148 => 149 [convolutional]                   
    x = _conv_block(x, [{'filter':  512, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ':  1, 'layer_idx': 148},
                        {'filter':  255, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ':  0, 'layer_idx': 149}])
    
    # -- end last Convulationnal 3*3 and 1*1 for 38*38
    
    # -- output 38*38  
    ## Layer 150 [yolo]
    yolo_150 = x   
   
# ---------------------------------- end head output 38*38 ---------------------------------------
    
    # ---------------------------------- begin head output 19*19 ---------------------------------------
    ## Layer 151 [route] layers = -4
    x = layer_147
        
    ## Layer 152 [convolutional]
    x = _conv_block(x, [{'filter':  512, 'kernel': 3, 'stride': 2, 'bnorm': True, 'activ': 1, 'layer_idx': 152}])
    layer_152 = x  
                        
    ## Layer 153 [route] layers = -1, -37                   
    x = concatenate([layer_152, layer_116],  name='concat_153') 
                        
    # -- begin Convulationnal set for 19*19                    
    ## Layer 154 => 160 [convolutional]
    x = _conv_block(x, [{'filter':   512, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ':    1, 'layer_idx': 154},
                        {'filter':  1024, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ':    1, 'layer_idx': 155},
                        {'filter':   512, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ':    1, 'layer_idx': 156},
                        {'filter':  1024, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ':    1, 'layer_idx': 157},  
                        {'filter':   512, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ':    1, 'layer_idx': 158}, 
    # -- end Convulationnal set for 19*19
                        
    # -- beging last Convulationnal 3*3 and 1*1 for 19*19                   
                        {'filter':  1024, 'kernel': 3, 'stride': 1, 'bnorm': True, 'activ':    1, 'layer_idx': 159},
                        {'filter':   255, 'kernel': 1, 'stride': 1, 'bnorm': True, 'activ':    0, 'layer_idx': 160}])  
    # -- end last Convulationnal 3*3 and 1*1 for 19*19                     
    
    # -- output 19*19                    
    ## Layer 161 [yolo]
    yolo_161 = x
    
    # ---------------------------------- end head output 19*19 ---------------------------------------                    
                        
    model = Model(input_image, [yolo_139, yolo_150, yolo_161], name = 'Yolo_v4')    
    return model