"""
废弃
"""
import threading
import cv2
import numpy as np
import func
import tkinter as tk
from PIL import Image, ImageTk

#gui需要更新的参数 摄像头和说明文本
current_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
exp_index=1
#说明文本与按钮
exp_texts=["弹出一个gui 让你框一个区域 然后后边镜像和检测都只在这个区域内实现 框选后再次点击则恢复",
           "让你的画面变成！？虽弓弓虽？！",
           "让你的画面变成\n！\n？\n虽\n弓\n弓\n虽\n？\n！\n",
           "弹出一个gui 让你框选一个区域 然后实时跟踪",
           "看看你的脸 能识别出你的年龄 性别 表情 但是这个模型疑似有点拉 识别的不是很准"]
button_texts=["框选ROI区域（后续效果仅作用于该区域）",
              "切换左右镜像",
              "切换上下镜像",
              "框选目标开始跟踪",
              "表情探测"]

# 跟踪器
tracker = cv2.TrackerCSRT_create()
flags=[False,False,False,False,False]

def toggle_flag(i):
    global flags
    flags[i]=not flags[i]

#多线程建立gui
def create_gui():
    root = tk.Tk()
    root.title("看镜头")
    root.geometry("1680x960")

    btm_frame=tk.Frame(root,bg="pink")
    btm_frame.pack(side=tk.BOTTOM,fill=tk.BOTH, expand=True,padx=10, pady=10)

    def show_tips(e,i):
        exp_label.config(text=exp_texts[i])

    def del_tips(e):
        exp_label.config(text=" ")

    for i in range(5):
        btn = tk.Button(btm_frame, text=button_texts[i],wraplength=120,command=lambda i=i: toggle_flag(i))
        btn.bind("<Enter>",lambda e,idx=i: show_tips(e,idx)) #调用函数传固定index
        btn.bind("<Leave>",del_tips)
        btn.grid(row=1, column=i, padx=80, pady=60, sticky=tk.NSEW)
        btm_frame.grid_columnconfigure(i, weight=1)


    # 初始化图片
    """
    tk需要tkimg 而提供的是array
    Image.fromarray array->img
    ImageTk.PhotoImage img->tkimg
    """
    pil_img = Image.fromarray(current_frame)
    tk_img = ImageTk.PhotoImage(image=pil_img)
    img_label = tk.Label(root, image=tk_img)
    img_label.image = tk_img
    img_label.pack(side=tk.LEFT,anchor=tk.NW,padx=20, pady=20)

    left_frame = tk.Frame(root,background="skyblue")
    left_frame.pack(side=tk.LEFT,fill=tk.BOTH,expand=True, padx=10, pady=10)

    spil_img = Image.open("../models/xh.jpg").resize((240, 240))
    stk_img = ImageTk.PhotoImage(spil_img)
    simg_label=tk.Label(left_frame,image=stk_img)
    simg_label.pack(anchor=tk.N,padx=20, pady=20)

    expt_label=tk.Label(left_frame,wraplength=200,text="这是什么？",background="skyblue")
    expt_label.pack(side=tk.LEFT,anchor=tk.NW,padx=20,pady=20)

    exp_label=tk.Label(left_frame,wraplength=200,background="skyblue")
    exp_label.pack(side=tk.LEFT, anchor=tk.NW, padx=20, pady=20)


    #更新函数 更新
    def refresh():
        global current_frame
        global exp_index
        try:
            show = cv2.cvtColor(current_frame, cv2.COLOR_BGR2RGB)
            new_pil = Image.fromarray(show)
            new_tk_img = ImageTk.PhotoImage(image=new_pil)
            img_label.config(image=new_tk_img)
            img_label.image = new_tk_img
            exp_label.text=exp_texts[exp_index]
        except:
            pass
        #每10毫秒更新
        root.after(10, refresh)
    refresh()

    def on_close():
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()

gui_thread = threading.Thread(target=create_gui, daemon=True)
gui_thread.start()

roi_x, roi_y, roi_w, roi_h = 0, 0, 1280, 720
tracking=False
selected=False

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

while True:
    ret, frame = cap.read()
    frame = cv2.flip(frame, 1)
    hi, wi = frame.shape[:2]
    frame_copy = frame.copy()
    key = cv2.waitKey(1) & 0xFF

    #标记True 但是上一次的标记为False 意味着执行 执行框选
    if flags[0] and selected==False:
        selsuc,roi= func.selectROI(frame_copy)
        if selsuc:
            roi_x,roi_y,roi_w,roi_h=roi

    #标记False 上一次为True 意味着终止 回复原状
    if flags[0]==False and selected:
        roi_x, roi_y, roi_w, roi_h = 0, 0, 1280, 720

    #同上
    if flags[3] and tracking==False:
        tracker= func.Track_select(frame_copy, tracker)

    #记录此次框选的标记
    selected=flags[0]
    tracking=flags[3]

    #根据框选返回的roi确定范围 并重新包装
    roi_x = max(0, roi_x)
    roi_y = max(0, roi_y)
    roi_w = min(roi_w, wi - roi_x)
    roi_h = min(roi_h, hi - roi_y)
    roi_region = frame_copy[roi_y:roi_y+roi_h, roi_x:roi_x+roi_w]
    rois=(roi_region,roi_x,roi_y,roi_w,roi_h)

    #调用功能函数 flag为false则不触发 true触发
    frame_copy= func.Tracking(frame_copy, flags[3], tracker)
    frame_copy= func.Mirror(frame_copy, flags[1], flags[2], rois)
    frame_copy= func.face_detect(frame_copy, flags[4], rois)

    #更新摄像头
    current_frame=frame_copy

