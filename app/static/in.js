/**
 * in.js —— 入口摄像头
 *
 * 现在只做一件事：用入口角色启动 CameraApp。
 * 全部公共逻辑（摄像头、帧发送、渲染、注册、录制、主题）都在 camera-core.js，
 * 与 out.js 不再有重复代码。
 *
 * 业务动作（签到）由服务端在 app/services/frame_pipeline.py 中完成，
 * 本文件不发送任何业务请求。
 */
(function () {
    'use strict';

    var app = new window.CameraApp({
        role: 'entry',
        wsPath: '/api/v1/ws/camera'
    });

    // 供调试：控制台可访问 window.__entryApp
    window.__entryApp = app;
})();
