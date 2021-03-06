from __future__ import absolute_import, division, print_function

import click
import cv2
import matplotlib.cm as cm
import numpy as np
import yaml
from addict import Dict

# ---- added for writing csv
import os
import datetime
import csv
import operator
# ---------------------------

import glob

# sort dict
from operator import itemgetter

from libs.models import *
from libs.utils import DenseCRF
### Using Places365 indoor train dataset

def get_device(cuda):
    cuda = cuda and torch.cuda.is_available()
    device = torch.device("cuda" if cuda else "cpu")
    if cuda:
        current_device = torch.cuda.current_device()
        print("Device:", torch.cuda.get_device_name(current_device))
    else:
        print("Device: CPU")
    return device

def get_classtable(CONFIG):
    with open(CONFIG.DATASET.LABELS) as f:
        classes = {}
        for label in f:
            label = label.rstrip().split("\t")
            classes[int(label[0])] = label[1]
            # classes[int(label[0])] = label[1].split(",")[0]
    return classes

def setup_postprocessor(CONFIG):
    # CRF post-processor
    postprocessor = DenseCRF(
        iter_max=CONFIG.CRF.ITER_MAX,
        pos_xy_std=CONFIG.CRF.POS_XY_STD,
        pos_w=CONFIG.CRF.POS_W,
        bi_xy_std=CONFIG.CRF.BI_XY_STD,
        bi_rgb_std=CONFIG.CRF.BI_RGB_STD,
        bi_w=CONFIG.CRF.BI_W,
    )
    return postprocessor


def preprocessing(image, device, CONFIG):
    # Resize
    scale = CONFIG.IMAGE.SIZE.TEST / max(image.shape[:2])
    image = cv2.resize(image, dsize=None, fx=scale, fy=scale)
    raw_image = image.astype(np.uint8)

    # Subtract mean values
    image = image.astype(np.float32)
    image -= np.array(
        [
            float(CONFIG.IMAGE.MEAN.B),
            float(CONFIG.IMAGE.MEAN.G),
            float(CONFIG.IMAGE.MEAN.R),
        ]
    )

    # Convert to torch.Tensor and add "batch" axis
    image = torch.from_numpy(image.transpose(2, 0, 1)).float().unsqueeze(0)
    image = image.to(device)

    return image, raw_image


def inference(model, image, raw_image=None, postprocessor=None):
    _, _, H, W = image.shape

    # Image -> Probability map
    logits = model(image)
    logits = F.interpolate(logits, size=(H, W), mode="bilinear", align_corners=False)
    probs = F.softmax(logits, dim=1)[0]
    probs = probs.cpu().numpy()

    # Refine the prob map with CRF
    if postprocessor and raw_image is not None:
        probs = postprocessor(raw_image, probs)

    labelmap = np.argmax(probs, axis=0)
    return labelmap

@click.group()
@click.pass_context
def main(ctx):
    """
    Demo with a trained model
    """

    print("Mode:", ctx.invoked_subcommand)


@main.command()
@click.option(
    "-c",
    "--config-path",
    type=click.File(),
    required=True,
    help="Dataset configuration file in YAML",
)
@click.option(
    "-m",
    "--model-path",
    type=click.Path(exists=True),
    required=True,
    help="PyTorch model to be loaded",
)
@click.option(
    "-i",
    "--image-path",
    type=click.Path(exists=True),
    required=True,
    help="Image to be processed",
)
@click.option(
    "--cuda/--cpu", default=True, help="Enable CUDA if available [default: --cuda]"
)
@click.option("--crf", is_flag=True, show_default=True, help="CRF post-processing")
def single(config_path, model_path, image_path, cuda, crf):
    """
    Inference from multiple images
    """

    # Setup
    CONFIG = Dict(yaml.load(config_path))
    device = get_device(cuda)
    torch.set_grad_enabled(False)

    classes = get_classtable(CONFIG)
    postprocessor = setup_postprocessor(CONFIG) if crf else None
    print(classes)
    model = eval(CONFIG.MODEL.NAME)(n_classes=CONFIG.DATASET.N_CLASSES)
    state_dict = torch.load(model_path, map_location=lambda storage, loc: storage)
    model.load_state_dict(state_dict)
    model.eval()
    model.to(device)
    print("Model:", CONFIG.MODEL.NAME)

    # ------------------------------------------------------------------------------
    # load place gt for images
    reader = csv.reader(open('dataset/MITPlaces_indoor/gt_MITPlaces_3_train.csv', 'r'))
    gt = {}
    for row in reader:
        k, v = row
        gt[k] = v

    # final output array
    weighted_labels = np.zeros((182, 3))
    #--------------------------------------------------------------------------------
    img_files = []
    count = 0
    for path, label in gt.items(): # skip "attic"
        if count == 0:
            count += 1
            continue
        else:
            img_files.append("./dataset/MITPlaces_indoor/" + path)

    for img_file in img_files:
        # Inference
        print(img_file)
        img_file_name = img_file.split('/')[3] # file name without path
        img_file = img_file.replace("\\", "/")
        image = cv2.imread(img_file, cv2.IMREAD_COLOR)
        if image is None:
            continue
        image, raw_image = preprocessing(image, device, CONFIG)
        labelmap = inference(model, image, raw_image, postprocessor)
        labels = np.unique(labelmap)

        image_label = int(gt[img_file_name])  # place label of an image

        for label in labels:
            print(label)
            # print(classes[label])
            # print(list(classes.keys())[list(classes.values()).index(label)])
            if image_label == 1: # image label == bathroom
                weighted_labels[label][0] += 1
            elif image_label == 2: # == bedroom
                weighted_labels[label][1] += 1
            elif image_label == 3: # == kitchen
                weighted_labels[label][2] += 1

    ### normalize
    for i in range(len(weighted_labels)):
        total = sum(weighted_labels[i])
        print(total)
        if total: # not 0
            for j in range(len(weighted_labels[i])):
                weighted_labels[i][j] /= total
    print(weighted_labels)
    np.savetxt('./data/csv/weighted_labels_places365_3.csv', weighted_labels, delimiter=",", fmt='%f')

@main.command()
@click.option(
    "-c",
    "--config-path",
    type=click.File(),
    required=True,
    help="Dataset configuration file in YAML",
)
@click.option(
    "-m",
    "--model-path",
    type=click.Path(exists=True),
    required=True,
    help="PyTorch model to be loaded",
)
@click.option(
    "--cuda/--cpu", default=True, help="Enable CUDA if available [default: --cuda]"
)
@click.option("--crf", is_flag=True, show_default=True, help="CRF post-processing")
@click.option("--camera-id", type=int, default=0, show_default=True, help="Device ID")
def live(config_path, model_path, cuda, crf, camera_id):
    pass


if __name__ == "__main__":
    main()
