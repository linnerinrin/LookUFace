"""
废弃
"""
from sklearn.metrics.pairwise import euclidean_distances
import cv2
import dlib
import numpy as np

#导入所需的模型
detector=dlib.get_frontal_face_detector()
predictor=dlib.shape_predictor("data/shape_predictor_68_face_landmarks.dat")

faceProto="data/opencv_face_detector.pbtxt"
faceModel="data/opencv_face_detector_uint8.pb"
faceNet=cv2.dnn.readNet(faceModel,faceProto)

ageProto="data/deploy_age.prototxt"
ageModel="data/age_net.caffemodel"
ageNet=cv2.dnn.readNet(ageModel,ageProto)
agelist=["0-4","4-8","8-12","12-20","20-38","38-48","48-60","60+"]

genderProto="data/deploy_gender.prototxt"
genderModel="data/gender_net.caffemodel"
genderNet=cv2.dnn.readNet(genderModel,genderProto)
genderlist=["Male","Female"]

#框选所选区域并且标注
def DrawAndWrite(frame,pois,poie,col,text,high):
    cv2.rectangle(frame,pois, poie,col, 2)
    cv2.putText(frame,text, (pois[0], pois[1]+20+high*20), cv2.FONT_HERSHEY_SIMPLEX, 0.6,col, 2)
    return frame

#面部特征参数 计算
def MAR(shape):
    hei1=euclidean_distances(shape[50].reshape(1,2),shape[58].reshape(1,2))
    hei2= euclidean_distances(shape[51].reshape(1, 2),shape[57].reshape(1, 2))
    hei3= euclidean_distances(shape[52].reshape(1, 2),shape[56].reshape(1, 2))
    width = euclidean_distances(shape[48].reshape(1, 2),shape[54].reshape(1, 2))
    return (hei1+hei2+hei3)/3/width
def MJR(shape):
    mouthwi=euclidean_distances(shape[48].reshape(1,2),shape[54].reshape(1,2))
    facewi=euclidean_distances(shape[3].reshape(1,2),shape[13].reshape(1,2))
    return mouthwi/facewi

#1.选择区域用 如果区域异常返回 false
def selectROI(frame):
    roi = cv2.selectROI("Select", frame, fromCenter=False, showCrosshair=True)
    roi_x, roi_y, roi_w, roi_h = roi
    if roi_w > 0 and roi_h > 0:
        selected = True
    else:
        selected = False
    cv2.destroyWindow("Select")
    return selected,roi

#2.跟踪区域用 同理1 没加异常处理
def Track_select(frame,tracker):
    roi_track = cv2.selectROI("select trackroi", frame, fromCenter=False, showCrosshair=True)
    cv2.destroyWindow("select trackroi")
    tracker.init(frame, roi_track)
    return tracker

#跟踪实现
def Tracking(frame,tracking,tracker):
    if tracking:
        suc, box = tracker.update(frame)
        if suc:
            x, y,w, h = [int(v) for v in box]
            frame=DrawAndWrite(frame,(x,y),(x+w,y+h),(0,255,0),"Tracking",1)
    return frame

#镜像 把图像一半复制到另一半
def Mirror(frame,mirrorlr,mirrorud,rois):
    roi,x,y,w,h=rois
    if mirrorlr:
        roi_flipped = cv2.flip(roi, 1)
        frame[y:y + h, x+w//2:x + w] = roi_flipped[0:h,w//2:w]
        frame=DrawAndWrite(frame,(x,y),(x+w,y+h),(255,0,0),"MirrorLR",1)
    if mirrorud:
        roi_flipped = cv2.flip(roi, 0)
        frame[y+h//2:y + h, x:x +w] =roi_flipped[h//2:h,0:w]
        frame=DrawAndWrite(frame,(x,y),(x+w,y+h),(0,255,255),"MirrorUD",1)
    return frame

def facepoi(frame,w,h):
    #这里是通过facenet计算脸部的位置 blob函数是把数据变成dnn需要的格式 参数为 图像 缩放 尺寸 均值 RB交换(opencv默认bgr  模型需要rgb) 裁剪
    blob = cv2.dnn.blobFromImage(frame, 1.0, (300, 300), [104, 117, 123], True, False)
    faceNet.setInput(blob)
    facect = faceNet.forward()
    faceBoxes = []
    for i in range(facect.shape[2]):
        conf = facect[0, 0, i, 2]
        if conf > 0.7:
            x1 = int(facect[0, 0, i, 3] * h)
            y1 = int(facect[0, 0, i, 4] * w)
            x2 = int(facect[0, 0, i, 5] * h)
            y2 = int(facect[0, 0, i, 6] * w)
            faceBoxes.append([x1, y1, x2, y2])
    return faceBoxes

def Forward(blob,net):
    net.setInput(blob)
    outs=net.forward()
    return outs[0].argmax()

def face_detect(frame,exp_status,rois):
    result1="None"
    result2="None"
    result3="None"
    roi, x, y, w, h = rois
    if exp_status:
        faceBoxes=facepoi(frame,w,h)
        if len(faceBoxes)>0:
            for faceBox in faceBoxes:
                x1,y1,x2,y2=faceBox
                face=frame[y1:y2,x1:x2]
                blob=cv2.dnn.blobFromImage(face,1.0,(227,227),(78,88,115))
                result1=genderlist[Forward(blob,genderNet)]
                result2=agelist[Forward(blob,ageNet)]

        faces = detector(roi, 0)
        for face in faces:
            shapes=np.array([[p.x,p.y] for p in predictor(roi,face).parts()])
            mar=MAR(shapes)
            mjr=MJR(shapes)
            if mar>0.5:
                result3="Laugh"
            elif mjr>0.45:
                result3="Smile"
        frame = DrawAndWrite(frame, (x, y), (x + w, y + h), (255,0,255), "Expection:" +result1+","+result2+","+result3,2)
    return frame
