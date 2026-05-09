import urllib.request
from pathlib import Path

MODEL_DIR = Path("../../models")
MODEL_DIR.mkdir(exist_ok=True)

models = [
    # 人脸检测模型
    ("https://raw.githubusercontent.com/opencv/opencv_extra/master/testdata/dnn/opencv_face_detector.pbtxt",
     "opencv_face_detector.pbtxt"),
    ("https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20180205_fp16/opencv_face_detector_uint8.pb",
     "opencv_face_detector_uint8.pb"),

    # 年龄识别模型
    ("https://raw.githubusercontent.com/opencv/opencv_extra/master/testdata/dnn/deploy_age.prototxt",
     "deploy_age.prototxt"),
    ("https://raw.githubusercontent.com/opencv/opencv_extra/master/testdata/dnn/age_net.caffemodel",
     "age_net.caffemodel"),

    # 性别识别模型
    ("https://raw.githubusercontent.com/opencv/opencv_extra/master/testdata/dnn/deploy_gender.prototxt",
     "deploy_gender.prototxt"),
    ("https://raw.githubusercontent.com/opencv/opencv_extra/master/testdata/dnn/gender_net.caffemodel",
     "gender_net.caffemodel"),
]

for url, filename in models:
    save_path = MODEL_DIR / filename
    if save_path.exists():
        print(f"文件已存在，跳过: {filename}")
        continue
    print(f"下载中: {filename}")
    try:
        urllib.request.urlretrieve(url, save_path)
    except Exception as e:
        print(f"  [失败] {filename}: {e}")

print("所有模型处理完成。")