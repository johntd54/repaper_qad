from pathlib import Path

import cv2
import fire
import numpy as np
import torch

from models import UNet


def main(model_type, model_path, input_image_path, output_image_path, gpu=True):
    """Perform stylizing of `input_image_path`

    # Arguments
        model_type [str]: 'unet'
        model_path [str]: the path to trained weight
        input_image_path [str]: the path to image to stylize
        output_image_path [str]: the path to saved output image
        gpu [bool]: whether to run stylizing with GPU or not
    """
    if gpu:
        chpt = torch.load(model_path)
    else:
        chpt = torch.load(model_path, map_location="cpu")

    if model_type == "unet":
        model = UNet()
    else:
        raise AttributeError('unknown model_type, should be unet')

    model.load_state_dict(chpt["model"])
    model.eval()

    image = cv2.cvtColor(cv2.imread(input_image_path), cv2.COLOR_BGR2RGB)
    print("Input shape", image.shape)
    image = torch.FloatTensor(image).permute(2, 0, 1).unsqueeze(0) / 255

    if gpu:
        model = model.cuda()
        image = image.cuda()

    pred = model(image)

    if model_type == "unet":
        output_image = (
            pred.cpu().squeeze().permute(1, 2, 0).data.numpy() * 255
        ).astype(np.uint8)

    cv2.imwrite(output_image_path, cv2.cvtColor(output_image, cv2.COLOR_RGB2BGR))


if __name__ == "__main__":
    fire.Fire(main)
