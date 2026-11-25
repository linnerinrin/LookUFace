/**
 * out.js —— 出口摄像头
 *
 * 用出口角色启动 CameraApp，全部公共逻辑都在 camera-core.js
 *
 * 出口语义（由服务端判定，见 app/services/frame_pipeline.py）：
 *   识别到在场者 -> 直接签退并计入在场时长
 */
(function () {
    'use strict';

    var app = new window.CameraApp({
        role: 'exit',
        wsPath: '/api/v1/ws/camera-exit'
    });

    window.__exitApp = app;
})();
