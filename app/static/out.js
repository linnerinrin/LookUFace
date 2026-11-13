/**
 * out.js —— 出口摄像头
 *
 * 用出口角色启动 CameraApp，全部公共逻辑都在 camera-core.js
 *
 * 出口语义（由服务端判定，见 app/services/frame_pipeline.py）：
 *   - 在线且未暂离的人被识别到 -> 自动暂离
 *   - 已在暂离的人被识别到     -> 自动签退并结算
 */
(function () {
    'use strict';

    var app = new window.CameraApp({
        role: 'exit',
        wsPath: '/api/v1/ws/camera-exit'
    });

    window.__exitApp = app;
})();
