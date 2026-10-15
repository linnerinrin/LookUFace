/**
 * out.js —— 出口摄像头
 *
 * 现在只做一件事：用出口角色启动 CameraApp。
 * 全部公共逻辑都在 camera-core.js，与 in.js 不再有重复代码。
 *
 * 出口语义（由服务端判定，见 app/services/frame_pipeline.py）：
 *   - 在线且未暂离的人被识别到 -> 自动暂离
 *   - 已在暂离的人被识别到     -> 自动签退并结算
 * 旧版靠前端累计 30 帧来「猜」这个时机（实际触发时间在 3~15 秒间浮动），
 * 且每帧都要发一次 /check/status + /check/checkout。
 */
(function () {
    'use strict';

    var app = new window.CameraApp({
        role: 'exit',
        wsPath: '/api/v1/ws/camera-exit'
    });

    window.__exitApp = app;
})();
