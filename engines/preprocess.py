# -*- coding: utf-8 -*-
"""共享图像预处理：HSV 红章/红色防伪抑制（发票必需）。
PaddleEngine 与 RapidOCREngine 共用，避免重复实现。"""
from __future__ import annotations


def suppress_red(arr):
    """把红章/红色防伪涂白，减轻对黑色文字的遮挡干扰。
    入参/返回均为 BGR ndarray。"""
    import cv2
    import numpy as np  # noqa
    hsv = cv2.cvtColor(arr, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, (0, 80, 60), (10, 255, 255))
    m2 = cv2.inRange(hsv, (160, 80, 60), (180, 255, 255))
    mask = cv2.bitwise_or(m1, m2)
    out = arr.copy()
    out[mask > 0] = (255, 255, 255)
    return out
