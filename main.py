#!/usr/bin/env python3
"""Single-file LAN viewer for a local MMD PMX/PMD model."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import posixpath
import signal
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


DEFAULT_MODEL_DIR = Path(__file__).resolve().parent / "len"
DEFAULT_PORT = 51943
# r172 removed the official MMDLoader. Pin the tested r170 release so the
# loader, parser, toon shader, and OutlineEffect stay version-aligned.
THREE_VERSION = "0.170.0"
HDR_ASSETS = {
    "studio.hdr": "royal_esplanade_1k.hdr",  # Backward-compatible alias.
    "royal-esplanade.hdr": "royal_esplanade_1k.hdr",
    "venice-sunset.hdr": "venice_sunset_1k.hdr",
    "pedestrian-overpass.hdr": "pedestrian_overpass_1k.hdr",
    "moonless-golf.hdr": "moonless_golf_1k.hdr",
    "quarry.hdr": "quarry_01_1k.hdr",
    "spruit-sunrise.hdr": "spruit_sunrise_1k.hdr",
    "blouberg-sunrise.hdr": "blouberg_sunrise_2_1k.hdr",
}
ALLOWED_ASSET_SUFFIXES = {
    ".pmx", ".pmd", ".png", ".jpg", ".jpeg", ".bmp", ".tga", ".spa", ".sph"
}
VENDOR_CACHE: dict[str, bytes] = {}
VENDOR_CACHE_LOCK = threading.Lock()


HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#101414">
  <title>Miku MMD Viewer</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101414;
      --surface: #171c1c;
      --surface-2: #202727;
      --line: #303a39;
      --line-strong: #465452;
      --text: #edf3f1;
      --muted: #9ca9a6;
      --accent: #4dd8ba;
      --accent-strong: #73efd3;
      --amber: #e7b45d;
      --danger: #ef7d79;
      --panel: 354px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-synthesis: none;
    }
    * { box-sizing: border-box; }
    html, body { width: 100%; height: 100%; margin: 0; overflow: hidden; background: var(--bg); color: var(--text); }
    button, input { font: inherit; letter-spacing: 0; }
    button { color: inherit; }
    #app { position: relative; width: 100%; height: 100%; isolation: isolate; }
    #viewport { position: absolute; inset: 0 var(--panel) 0 0; overflow: hidden; background: #cad5d3; }
    #viewport canvas { display: block; width: 100%; height: 100%; touch-action: none; }
    #inspector {
      position: absolute; z-index: 10; inset: 0 0 0 auto; width: var(--panel);
      display: flex; flex-direction: column; background: rgba(18, 23, 23, .98);
      border-left: 1px solid var(--line); box-shadow: -12px 0 28px rgba(0,0,0,.16);
    }
    .brand { height: 62px; flex: 0 0 62px; display: flex; align-items: center; gap: 12px; padding: 0 16px; border-bottom: 1px solid var(--line); }
    .brand-mark { width: 28px; height: 28px; display: grid; place-items: center; border: 1px solid var(--accent); color: var(--accent); border-radius: 6px; font-weight: 750; }
    .brand-copy { min-width: 0; flex: 1; }
    .brand-title { margin: 0; font-size: 14px; font-weight: 680; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .brand-meta { margin-top: 3px; color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
    .icon-button { width: 34px; height: 34px; flex: 0 0 34px; display: grid; place-items: center; padding: 0; border: 1px solid var(--line); border-radius: 6px; background: var(--surface-2); cursor: pointer; }
    .icon-button:hover { border-color: var(--line-strong); background: #283130; }
    .icon-button svg { width: 17px; height: 17px; }
    #panel-toggle { display: none; }
    .tabs { display: grid; grid-template-columns: repeat(5, 1fr); flex: 0 0 43px; border-bottom: 1px solid var(--line); }
    .tab { position: relative; border: 0; background: transparent; color: var(--muted); font-size: 12px; cursor: pointer; }
    .tab:hover { color: var(--text); }
    .tab.active { color: var(--accent-strong); }
    .tab.active::after { content: ""; position: absolute; height: 2px; left: 14px; right: 14px; bottom: 0; background: var(--accent); }
    .panels { flex: 1; min-height: 0; overflow: auto; overscroll-behavior: contain; scrollbar-color: var(--line-strong) transparent; }
    .panel { display: none; padding-bottom: 24px; }
    .panel.active { display: block; }
    .section { padding: 16px; border-bottom: 1px solid var(--line); }
    .section-head { display: flex; align-items: center; justify-content: space-between; min-height: 22px; margin-bottom: 13px; }
    .section h2 { margin: 0; font-size: 12px; font-weight: 700; color: #cbd5d2; }
    .readout { color: var(--accent-strong); font-size: 11px; font-variant-numeric: tabular-nums; }
    .control { display: grid; grid-template-columns: 91px minmax(0,1fr) 46px; align-items: center; gap: 9px; min-height: 32px; }
    .control + .control { margin-top: 8px; }
    .control label { color: var(--muted); font-size: 12px; white-space: nowrap; }
    .value { text-align: right; color: #cbd5d2; font-size: 11px; font-variant-numeric: tabular-nums; }
    input[type="range"] { width: 100%; height: 18px; margin: 0; accent-color: var(--accent); cursor: pointer; }
    input[type="color"] { width: 42px; height: 24px; padding: 2px; border: 1px solid var(--line-strong); border-radius: 5px; background: var(--surface-2); cursor: pointer; }
    .color-control { grid-template-columns: 1fr auto; }
    .switch-row { display: flex; align-items: center; justify-content: space-between; min-height: 34px; color: var(--muted); font-size: 12px; }
    .switch { position: relative; width: 36px; height: 20px; }
    .switch input { position: absolute; opacity: 0; pointer-events: none; }
    .switch span { position: absolute; inset: 0; border: 1px solid var(--line-strong); border-radius: 10px; background: #252d2c; cursor: pointer; transition: .18s ease; }
    .switch span::after { content: ""; position: absolute; width: 14px; height: 14px; left: 2px; top: 2px; border-radius: 50%; background: #9ba8a5; transition: .18s ease; }
    .switch input:checked + span { border-color: var(--accent); background: #19493f; }
    .switch input:checked + span::after { left: 18px; background: var(--accent-strong); }
    .button-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .button-row.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .button-row.four { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .command { min-height: 34px; display: inline-flex; align-items: center; justify-content: center; gap: 7px; border: 1px solid var(--line); border-radius: 6px; background: var(--surface-2); color: #dce5e2; font-size: 12px; cursor: pointer; }
    .command:hover { border-color: var(--line-strong); background: #283130; }
    .command.primary { border-color: #287c6a; background: #1b5549; color: #d9fff7; }
    .command:disabled { opacity: .38; cursor: default; }
    .command svg { width: 15px; height: 15px; }
    .search { width: 100%; height: 34px; padding: 0 10px; border: 1px solid var(--line); border-radius: 6px; outline: 0; background: #111616; color: var(--text); }
    .search:focus { border-color: var(--accent); }
    .select { width: 100%; height: 36px; padding: 0 30px 0 10px; border: 1px solid var(--line); border-radius: 6px; outline: 0; background: #111616; color: var(--text); cursor: pointer; }
    .select:focus { border-color: var(--accent); }
    .segmented { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 2px; padding: 2px; border: 1px solid var(--line); border-radius: 6px; background: #111616; }
    .segment { min-height: 30px; border: 0; border-radius: 4px; background: transparent; color: var(--muted); font-size: 12px; cursor: pointer; }
    .segment.active { background: #275b50; color: #ddfff7; }
    .axis-x label, .axis-x .value { color: #ef8d89; }
    .axis-y label, .axis-y .value { color: #79d6a6; }
    .axis-z label, .axis-z .value { color: #86b9f3; }
    #pose-selection { display: none; }
    #pose-selection.visible { display: flex; }
    #pose-selection strong { max-width: 132px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .status-line { min-height: 30px; display: flex; align-items: center; justify-content: space-between; gap: 10px; color: var(--muted); font-size: 11px; }
    .status-line strong { color: var(--accent-strong); font-weight: 650; }
    .preset-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }
    .preset-grid .command { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .timeline { width: 100%; margin: 8px 0 2px; }
    .file-input { display: none; }
    #bone-label { position: absolute; z-index: 6; display: none; transform: translate(-50%, calc(-100% - 15px)); padding: 4px 7px; border: 1px solid rgba(31,88,76,.35); border-radius: 5px; background: rgba(239,249,246,.9); color: #215346; font-size: 11px; pointer-events: none; white-space: nowrap; box-shadow: 0 3px 10px rgba(18,35,31,.13); }
    #recording-indicator { display: none; color: var(--danger); }
    #recording-indicator.active { display: inline; }
    #morph-list { margin-top: 12px; }
    .empty { padding: 20px 0; color: var(--muted); font-size: 12px; text-align: center; }
    #hud { position: absolute; z-index: 4; top: 14px; left: 14px; display: flex; gap: 7px; pointer-events: none; }
    .hud-chip { min-height: 28px; display: flex; align-items: center; gap: 7px; padding: 0 9px; border: 1px solid rgba(25,32,31,.18); border-radius: 6px; background: rgba(245,249,248,.84); color: #25302e; box-shadow: 0 5px 16px rgba(15,20,19,.1); backdrop-filter: blur(10px); font-size: 11px; font-variant-numeric: tabular-nums; }
    #load-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--amber); box-shadow: 0 0 0 3px rgba(231,180,93,.18); }
    #load-dot.ready { background: #2ea987; box-shadow: 0 0 0 3px rgba(46,169,135,.18); }
    #load-dot.error { background: var(--danger); box-shadow: 0 0 0 3px rgba(239,125,121,.18); }
    #error-box { position: absolute; z-index: 5; left: 50%; top: 50%; width: min(520px, calc(100% - 36px)); transform: translate(-50%, -50%); padding: 18px; border: 1px solid rgba(131,50,48,.4); border-radius: 8px; background: rgba(46,22,22,.95); color: #ffdcd9; display: none; }
    #error-box strong { display: block; margin-bottom: 7px; font-size: 13px; }
    #error-box span { color: #e7b4b0; font-size: 12px; line-height: 1.55; overflow-wrap: anywhere; }
    #loading { position: absolute; z-index: 3; left: 50%; top: 50%; width: min(320px, calc(100% - 48px)); transform: translate(-50%, -50%); text-align: center; color: #31403d; }
    #loading-name { font-size: 13px; font-weight: 650; }
    .progress-track { height: 3px; margin-top: 12px; overflow: hidden; background: rgba(28,44,41,.16); }
    #progress { width: 2%; height: 100%; background: #208d75; transition: width .15s ease; }
    #loading-percent { margin-top: 7px; color: #5c6d69; font-size: 11px; font-variant-numeric: tabular-nums; }
    .mobile-open { display: none; }
    @media (max-width: 760px) {
      :root { --panel: min(100vw, 390px); }
      #viewport { inset: 0; }
      #inspector { width: min(92vw, 390px); transform: translateX(100%); transition: transform .22s ease; }
      #inspector.open { transform: translateX(0); }
      #panel-toggle { display: grid; }
      .mobile-open { position: absolute; z-index: 8; right: 12px; top: 12px; display: inline-flex; }
      #hud { top: 12px; left: 12px; right: 58px; flex-wrap: wrap; }
      .hud-chip:nth-child(3) { display: none; }
    }
    @media (max-width: 420px) {
      .brand { height: 56px; flex-basis: 56px; }
      .control { grid-template-columns: 82px minmax(0,1fr) 43px; gap: 7px; }
      .section { padding: 14px; }
    }
  </style>
  <script type="importmap">
    {"imports":{"three":"/vendor/build/three.module.js","three/addons/":"/vendor/examples/jsm/"}}
  </script>
</head>
<body>
<main id="app">
  <section id="viewport" aria-label="3D 模型视口">
    <div id="hud">
      <div class="hud-chip"><span id="load-dot"></span><span id="status">准备资源</span></div>
      <div class="hud-chip"><span id="fps">-- FPS</span></div>
      <div class="hud-chip"><span id="camera-summary">50 mm · 39.6°</span></div>
      <div class="hud-chip" id="pose-selection"><span>骨骼</span><strong id="pose-selection-name">--</strong></div>
      <div class="hud-chip" id="recording-indicator">● REC</div>
    </div>
    <div id="bone-label">--</div>
    <button id="mobile-open" class="icon-button mobile-open" title="打开参数面板" aria-label="打开参数面板"><i data-lucide="sliders-horizontal"></i></button>
    <div id="loading">
      <div id="loading-name">载入 MMD 模型</div>
      <div class="progress-track"><div id="progress"></div></div>
      <div id="loading-percent">0%</div>
    </div>
    <div id="error-box"><strong>模型载入失败</strong><span id="error-message"></span></div>
  </section>

  <aside id="inspector">
    <header class="brand">
      <div class="brand-mark">M</div>
      <div class="brand-copy">
        <h1 class="brand-title" id="model-name">Miku MMD Viewer</h1>
        <div class="brand-meta" id="model-meta">PMX · 载入中</div>
      </div>
      <button id="panel-toggle" class="icon-button" title="关闭参数面板" aria-label="关闭参数面板"><i data-lucide="panel-right-close"></i></button>
    </header>

    <nav class="tabs" aria-label="参数分类">
      <button class="tab active" data-panel="camera-panel">镜头</button>
      <button class="tab" data-panel="light-panel">灯光</button>
      <button class="tab" data-panel="pose-panel">动作</button>
      <button class="tab" data-panel="model-panel">模型</button>
      <button class="tab" data-panel="morph-panel">表情</button>
    </nav>

    <div class="panels">
      <div class="panel active" id="camera-panel">
        <section class="section">
          <div class="section-head"><h2>光学</h2><span class="readout" id="fov-readout">39.6°</span></div>
          <div class="control"><label for="focal">焦段</label><input id="focal" type="range" min="18" max="120" step="1" value="50"><span class="value" data-for="focal">50 mm</span></div>
          <div class="control"><label for="sensor">传感器</label><input id="sensor" type="range" min="24" max="50" step="1" value="36"><span class="value" data-for="sensor">36 mm</span></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>机位</h2><span class="readout" id="distance-readout">--</span></div>
          <div class="control"><label for="cam-azimuth">水平角</label><input id="cam-azimuth" type="range" min="-180" max="180" step="1" value="0"><span class="value" data-for="cam-azimuth">0°</span></div>
          <div class="control"><label for="cam-elevation">俯仰角</label><input id="cam-elevation" type="range" min="-75" max="75" step="1" value="4"><span class="value" data-for="cam-elevation">4°</span></div>
          <div class="control"><label for="cam-distance">距离</label><input id="cam-distance" type="range" min="0.55" max="4" step="0.01" value="1.4"><span class="value" data-for="cam-distance">1.40×</span></div>
          <div class="control"><label for="target-height">注视高度</label><input id="target-height" type="range" min="0.15" max="0.9" step="0.01" value="0.54"><span class="value" data-for="target-height">54%</span></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>构图预设与书签</h2><span class="readout" id="camera-preset-status">自由机位</span></div>
          <select id="camera-preset" class="select" aria-label="镜头构图预设">
            <option value="full">正面全身</option><option value="bust">正面半身</option><option value="portrait">面部特写</option><option value="three-quarter">三分之四视角</option><option value="profile">侧面视角</option><option value="low-angle">低机位全身</option>
          </select>
          <button id="apply-camera-preset" class="command" style="width:100%;margin-top:8px"><i data-lucide="scan"></i>应用构图</button>
          <select id="camera-bookmark" class="select" aria-label="镜头书签" style="margin-top:8px"><option value="1">机位书签 1</option><option value="2">机位书签 2</option><option value="3">机位书签 3</option><option value="4">机位书签 4</option></select>
          <div class="button-row" style="margin-top:8px"><button id="save-camera-bookmark" class="command"><i data-lucide="save"></i>保存</button><button id="load-camera-bookmark" class="command"><i data-lucide="folder-open"></i>载入</button></div>
        </section>
        <section class="section">
          <div class="button-row three">
            <button id="reset-camera" class="command primary"><i data-lucide="rotate-ccw"></i>复位</button>
            <button id="screenshot" class="command"><i data-lucide="camera"></i>截图</button>
            <button id="fullscreen" class="command"><i data-lucide="maximize"></i>全屏</button>
          </div>
        </section>
        <section class="section">
          <div class="section-head"><h2>图像与视频</h2><span class="readout" id="capture-status">就绪</span></div>
          <select id="capture-resolution" class="select" aria-label="导出分辨率">
            <option value="viewport">当前视口</option><option value="1920x1080">1080p</option><option value="2560x1440">2K</option><option value="3840x2160">4K</option>
          </select>
          <div class="switch-row"><span>透明截图背景</span><label class="switch"><input id="capture-transparent" type="checkbox"><span></span></label></div>
          <div class="control"><label for="turntable-duration">转台时长</label><input id="turntable-duration" type="range" min="2" max="12" step="1" value="6"><span class="value" data-for="turntable-duration">6 秒</span></div>
          <button id="record-turntable" class="command" style="width:100%;margin-top:9px"><i data-lucide="video"></i>录制转台 WebM</button>
        </section>
      </div>

      <div class="panel" id="light-panel">
        <section class="section">
          <div class="section-head"><h2>主光</h2><span class="readout" id="key-direction">右前 · 42°</span></div>
          <div class="control"><label for="key-intensity">亮度</label><input id="key-intensity" type="range" min="0" max="6" step="0.05" value="2.6"><span class="value" data-for="key-intensity">2.60</span></div>
          <div class="control"><label for="light-azimuth">水平角</label><input id="light-azimuth" type="range" min="-180" max="180" step="1" value="38"><span class="value" data-for="light-azimuth">38°</span></div>
          <div class="control"><label for="light-elevation">高度角</label><input id="light-elevation" type="range" min="-10" max="90" step="1" value="42"><span class="value" data-for="light-elevation">42°</span></div>
          <div class="control"><label for="shadow-softness">阴影柔度</label><input id="shadow-softness" type="range" min="0" max="8" step="0.5" value="3"><span class="value" data-for="shadow-softness">3.0</span></div>
          <div class="control color-control"><label for="key-color">颜色</label><input id="key-color" type="color" value="#fff4e4"></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>环境与补光</h2></div>
          <div class="control"><label for="ambient-intensity">环境亮度</label><input id="ambient-intensity" type="range" min="0" max="3" step="0.05" value="1.15"><span class="value" data-for="ambient-intensity">1.15</span></div>
          <div class="control"><label for="fill-intensity">补光亮度</label><input id="fill-intensity" type="range" min="0" max="3" step="0.05" value="0.55"><span class="value" data-for="fill-intensity">0.55</span></div>
          <div class="control color-control"><label for="fill-color">补光颜色</label><input id="fill-color" type="color" value="#b9ddff"></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>色彩管理</h2></div>
          <div class="control"><label for="exposure">曝光</label><input id="exposure" type="range" min="0.25" max="2.5" step="0.05" value="1"><span class="value" data-for="exposure">1.00</span></div>
          <div class="control color-control"><label for="background">背景</label><input id="background" type="color" value="#cad5d3"></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>全景与后期</h2><span class="readout" id="environment-status">纯色背景</span></div>
          <div class="control"><label for="hdr-preset">360° HDR 全景</label><select id="hdr-preset" class="select">
            <option value="royal-esplanade">室内商场 · Royal Esplanade</option>
            <option value="venice-sunset">水城日落 · Venice Sunset</option>
            <option value="pedestrian-overpass">城市天桥 · Pedestrian Overpass</option>
            <option value="moonless-golf">无月球场 · Moonless Golf</option>
            <option value="quarry">岩石采场 · Quarry</option>
            <option value="spruit-sunrise">河谷日出 · Spruit Sunrise</option>
            <option value="blouberg-sunrise">海岸日出 · Blouberg Sunrise</option>
          </select></div>
          <div class="switch-row"><span>显示 HDR 全景背景</span><label class="switch"><input id="hdr-environment" type="checkbox"><span></span></label></div>
          <div class="control"><label for="environment-intensity">环境强度</label><input id="environment-intensity" type="range" min="0" max="2" step="0.05" value="0.7"><span class="value" data-for="environment-intensity">0.70</span></div>
          <div class="control"><label for="hdr-rotation">全景方向</label><input id="hdr-rotation" type="range" min="-180" max="180" step="1" value="0"><span class="value" data-for="hdr-rotation">0°</span></div>
          <div class="control"><label for="background-intensity">背景亮度</label><input id="background-intensity" type="range" min="0" max="2" step="0.05" value="0.7"><span class="value" data-for="background-intensity">0.70</span></div>
          <div class="control"><label for="background-blur">背景模糊</label><input id="background-blur" type="range" min="0" max="1" step="0.01" value="0"><span class="value" data-for="background-blur">0%</span></div>
          <div class="switch-row"><span>泛光</span><label class="switch"><input id="bloom-enabled" type="checkbox"><span></span></label></div>
          <div class="control"><label for="bloom-strength">泛光强度</label><input id="bloom-strength" type="range" min="0" max="2" step="0.05" value="0.35"><span class="value" data-for="bloom-strength">0.35</span></div>
          <div class="switch-row"><span>景深</span><label class="switch"><input id="dof-enabled" type="checkbox"><span></span></label></div>
          <div class="control"><label for="dof-focus">对焦距离</label><input id="dof-focus" type="range" min="1" max="100" step="0.5" value="28"><span class="value" data-for="dof-focus">28.0</span></div>
          <div class="control"><label for="dof-blur">虚化强度</label><input id="dof-blur" type="range" min="0" max="0.02" step="0.0005" value="0.003"><span class="value" data-for="dof-blur">0.0030</span></div>
        </section>
      </div>

      <div class="panel" id="pose-panel">
        <section class="section">
          <div class="section-head"><h2>选择</h2><span class="readout" id="bone-count">0 根</span></div>
          <select id="bone-category" class="select" aria-label="骨骼分类" style="margin-bottom:8px"><option value="all">全部骨骼</option><option value="favorite">收藏</option><option value="body">躯干</option><option value="head">头部</option><option value="arm">手臂与手指</option><option value="leg">腿部</option><option value="physics">头发与物理</option><option value="other">其他</option></select>
          <select id="bone-select" class="select" aria-label="选择骨骼"><option value="">模型载入后显示</option></select>
          <button id="favorite-bone" class="command" style="width:100%;margin-top:8px"><i data-lucide="star"></i>收藏当前骨骼</button>
          <div class="switch-row"><span>点击模型选骨骼</span><label class="switch"><input id="surface-select" type="checkbox" checked><span></span></label></div>
          <div class="switch-row"><span>显示骨架</span><label class="switch"><input id="show-skeleton" type="checkbox"><span></span></label></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>鼠标操纵器</h2><span class="readout" id="pose-mode-readout">旋转 · 本地</span></div>
          <div class="segmented" id="pose-mode">
            <button class="segment active" data-mode="rotate">旋转</button>
            <button class="segment" data-mode="translate">移动</button>
          </div>
          <div class="segmented" id="pose-space" style="margin-top:8px">
            <button class="segment active" data-space="local">本地坐标</button>
            <button class="segment" data-space="world">世界坐标</button>
          </div>
          <div class="control" style="margin-top:11px"><label for="gizmo-size">操纵器大小</label><input id="gizmo-size" type="range" min="0.55" max="1.8" step="0.05" value="1"><span class="value" data-for="gizmo-size">1.00×</span></div>
          <div class="switch-row"><span>关节角度限制</span><label class="switch"><input id="joint-limits" type="checkbox" checked><span></span></label></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>IK 关键控制柄</h2><span class="readout" id="ik-status">关闭</span></div>
          <select id="ik-handle" class="select" aria-label="IK 控制柄"><option value="">模型载入后显示</option></select>
          <div class="switch-row"><span>启用控制柄</span><label class="switch"><input id="ik-enabled" type="checkbox"><span></span></label></div>
          <div class="control"><label for="ik-iterations">求解精度</label><input id="ik-iterations" type="range" min="2" max="20" step="1" value="10"><span class="value" data-for="ik-iterations">10 次</span></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>镜像与对称</h2><span class="readout" id="mirror-status">独立</span></div>
          <div class="button-row"><button id="mirror-copy" class="command"><i data-lucide="copy"></i>镜像复制</button><button id="swap-sides" class="command"><i data-lucide="arrow-left-right"></i>交换左右</button></div>
          <div class="switch-row"><span>实时对称编辑</span><label class="switch"><input id="live-mirror" type="checkbox"><span></span></label></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>手势与姿势模板</h2><span class="readout" id="pose-preset-status">待选择</span></div>
          <select id="hand-preset-side" class="select" aria-label="手势应用范围"><option value="both">双手</option><option value="left">左手</option><option value="right">右手</option></select>
          <div class="preset-grid" style="margin-top:8px"><button class="command hand-preset" data-hand="open">张开</button><button class="command hand-preset" data-hand="relaxed">放松</button><button class="command hand-preset" data-hand="fist">握拳</button><button class="command hand-preset" data-hand="point">指向</button><button class="command hand-preset" data-hand="peace">剪刀手</button></div>
          <select id="body-preset" class="select" aria-label="全身姿势模板" style="margin-top:8px"><option value="neutral">基础姿态</option><option value="arms-down">自然垂臂</option><option value="relaxed">轻松站姿</option><option value="wave-left">左手挥手</option><option value="wave-right">右手挥手</option><option value="contrapposto">重心站姿</option></select>
          <button id="apply-body-preset" class="command" style="width:100%;margin-top:8px"><i data-lucide="person-standing"></i>应用全身模板</button>
        </section>
        <section class="section">
          <div class="section-head"><h2>旋转偏移</h2><span class="readout">XYZ</span></div>
          <div class="control axis-x"><label for="pose-rot-x">X</label><input id="pose-rot-x" type="range" min="-180" max="180" step="1" value="0"><span class="value" data-for="pose-rot-x">0°</span></div>
          <div class="control axis-y"><label for="pose-rot-y">Y</label><input id="pose-rot-y" type="range" min="-180" max="180" step="1" value="0"><span class="value" data-for="pose-rot-y">0°</span></div>
          <div class="control axis-z"><label for="pose-rot-z">Z</label><input id="pose-rot-z" type="range" min="-180" max="180" step="1" value="0"><span class="value" data-for="pose-rot-z">0°</span></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>位置偏移</h2><span class="readout">XYZ</span></div>
          <div class="control axis-x"><label for="pose-pos-x">X</label><input id="pose-pos-x" type="range" min="-5" max="5" step="0.01" value="0"><span class="value" data-for="pose-pos-x">0.00</span></div>
          <div class="control axis-y"><label for="pose-pos-y">Y</label><input id="pose-pos-y" type="range" min="-5" max="5" step="0.01" value="0"><span class="value" data-for="pose-pos-y">0.00</span></div>
          <div class="control axis-z"><label for="pose-pos-z">Z</label><input id="pose-pos-z" type="range" min="-5" max="5" step="0.01" value="0"><span class="value" data-for="pose-pos-z">0.00</span></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>历史与姿势文件</h2><span class="readout" id="history-status">0 步</span></div>
          <div class="button-row"><button id="undo-pose" class="command" disabled><i data-lucide="undo-2"></i>撤销</button><button id="redo-pose" class="command" disabled><i data-lucide="redo-2"></i>重做</button></div>
          <div class="button-row" style="margin-top:8px"><button id="export-pose" class="command"><i data-lucide="download"></i>导出姿势</button><button id="import-pose" class="command"><i data-lucide="upload"></i>导入姿势</button></div>
          <input id="pose-file" class="file-input" type="file" accept="application/json,.json">
          <button id="load-vpd" class="command" style="width:100%;margin-top:8px"><i data-lucide="file-up"></i>导入 VPD 姿势</button>
          <input id="vpd-file" class="file-input" type="file" accept=".vpd,application/octet-stream">
          <select id="pose-slot" class="select" aria-label="姿势槽位" style="margin-top:8px"><option value="1">姿势槽位 1</option><option value="2">姿势槽位 2</option><option value="3">姿势槽位 3</option><option value="4">姿势槽位 4</option></select>
          <div class="button-row" style="margin-top:8px"><button id="save-slot" class="command"><i data-lucide="save"></i>保存槽位</button><button id="load-slot" class="command"><i data-lucide="folder-open"></i>载入槽位</button></div>
        </section>
        <section class="section">
          <div class="button-row">
            <button id="reset-selected-bone" class="command"><i data-lucide="undo-2"></i>当前复位</button>
            <button id="reset-all-pose" class="command primary"><i data-lucide="refresh-cw"></i>全身复位</button>
          </div>
        </section>
      </div>

      <div class="panel" id="model-panel">
        <section class="section">
          <div class="section-head"><h2>模型与附加项</h2><span class="readout" id="model-library-status">主模型</span></div>
          <select id="model-select" class="select" aria-label="服务端模型"><option value="">正在读取模型目录</option></select>
          <div class="button-row" style="margin-top:8px"><button id="switch-model" class="command primary"><i data-lucide="refresh-cw"></i>切换主模型</button><button id="add-server-accessory" class="command"><i data-lucide="plus"></i>添加附加项</button></div>
          <button id="add-local-accessory" class="command" style="width:100%;margin-top:8px"><i data-lucide="folder-open"></i>选择本地模型目录</button>
          <input id="local-accessory-files" class="file-input" type="file" webkitdirectory directory multiple>
          <select id="accessory-select" class="select" aria-label="附加模型" style="margin-top:8px"><option value="">暂无附加项</option></select>
          <button id="remove-accessory" class="command" style="width:100%;margin-top:8px" disabled><i data-lucide="trash-2"></i>移除当前附加项</button>
        </section>
        <section class="section">
          <div class="section-head"><h2>附加项变换</h2><span class="readout" id="accessory-status">未选择</span></div>
          <div class="control"><label for="accessory-x">水平 X</label><input id="accessory-x" type="range" min="-30" max="30" step="0.05" value="0" disabled><span class="value" data-for="accessory-x">0.00</span></div>
          <div class="control"><label for="accessory-y">高度 Y</label><input id="accessory-y" type="range" min="-10" max="30" step="0.05" value="0" disabled><span class="value" data-for="accessory-y">0.00</span></div>
          <div class="control"><label for="accessory-z">纵深 Z</label><input id="accessory-z" type="range" min="-30" max="30" step="0.05" value="0" disabled><span class="value" data-for="accessory-z">0.00</span></div>
          <div class="control"><label for="accessory-yaw">朝向</label><input id="accessory-yaw" type="range" min="-180" max="180" step="1" value="0" disabled><span class="value" data-for="accessory-yaw">0°</span></div>
          <div class="control"><label for="accessory-scale">缩放</label><input id="accessory-scale" type="range" min="0.1" max="3" step="0.01" value="1" disabled><span class="value" data-for="accessory-scale">1.00×</span></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>变换</h2><span class="readout" id="model-size">--</span></div>
          <div class="control"><label for="model-yaw">朝向</label><input id="model-yaw" type="range" min="-180" max="180" step="1" value="0"><span class="value" data-for="model-yaw">0°</span></div>
          <div class="control"><label for="model-scale">缩放</label><input id="model-scale" type="range" min="0.5" max="1.5" step="0.01" value="1"><span class="value" data-for="model-scale">1.00×</span></div>
          <div class="switch-row"><span>自动旋转</span><label class="switch"><input id="auto-rotate" type="checkbox"><span></span></label></div>
          <div class="control"><label for="rotate-speed">旋转速度</label><input id="rotate-speed" type="range" min="0.1" max="2" step="0.1" value="0.5"><span class="value" data-for="rotate-speed">0.5×</span></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>VMD 动作库</h2><span class="readout" id="animation-status">未载入</span></div>
          <button id="load-vmd" class="command" style="width:100%"><i data-lucide="file-up"></i>添加人物 VMD</button>
          <input id="vmd-file" class="file-input" type="file" accept=".vmd,application/octet-stream" multiple>
          <select id="motion-select" class="select" aria-label="人物 VMD 动作" style="margin-top:8px"><option value="">尚未添加动作</option></select>
          <input id="animation-time" class="timeline" type="range" min="0" max="1" step="0.001" value="0" disabled>
          <div class="status-line"><span id="animation-time-label">00:00.000 / 00:00.000</span><span id="animation-file">--</span></div>
          <div class="button-row three"><button id="animation-play" class="command" disabled><i data-lucide="play"></i>播放</button><button id="animation-stop" class="command" disabled><i data-lucide="square"></i>停止</button><button id="animation-remove" class="command" disabled><i data-lucide="x"></i>移除</button></div>
          <div class="control" style="margin-top:10px"><label for="animation-speed">播放速度</label><input id="animation-speed" type="range" min="0.1" max="2" step="0.05" value="1"><span class="value" data-for="animation-speed">1.00×</span></div>
          <div class="control"><label for="animation-fade">动作淡入淡出</label><input id="animation-fade" type="range" min="0" max="2" step="0.1" value="0.4"><span class="value" data-for="animation-fade">0.4 秒</span></div>
          <div class="switch-row"><span>循环播放</span><label class="switch"><input id="animation-loop" type="checkbox" checked><span></span></label></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>相机 VMD</h2><span class="readout" id="camera-vmd-status">未载入</span></div>
          <div class="button-row"><button id="load-camera-vmd" class="command"><i data-lucide="video"></i>选择文件</button><button id="remove-camera-vmd" class="command" disabled><i data-lucide="x"></i>移除</button></div>
          <input id="camera-vmd-file" class="file-input" type="file" accept=".vmd,application/octet-stream">
          <div class="switch-row"><span>跟随 VMD 镜头</span><label class="switch"><input id="camera-vmd-enabled" type="checkbox" disabled><span></span></label></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>音频同步</h2><span class="readout" id="audio-status">未载入</span></div>
          <div class="button-row"><button id="load-audio" class="command"><i data-lucide="music"></i>选择音频</button><button id="remove-audio" class="command" disabled><i data-lucide="x"></i>移除</button></div>
          <input id="audio-file" class="file-input" type="file" accept="audio/*,.wav,.mp3,.ogg,.m4a,.flac">
          <div class="control"><label for="audio-delay">音频延迟</label><input id="audio-delay" type="range" min="-5" max="5" step="0.05" value="0"><span class="value" data-for="audio-delay">0.00 秒</span></div>
          <div class="control"><label for="audio-volume">音量</label><input id="audio-volume" type="range" min="0" max="1" step="0.01" value="0.8"><span class="value" data-for="audio-volume">80%</span></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>头发与裙摆物理</h2><span class="readout" id="physics-status">默认关闭</span></div>
          <div class="switch-row"><span>启用 Ammo 物理</span><label class="switch"><input id="physics-enabled" type="checkbox"><span></span></label></div>
          <div class="control"><label for="physics-gravity">重力强度</label><input id="physics-gravity" type="range" min="0" max="2" step="0.05" value="1"><span class="value" data-for="physics-gravity">1.00×</span></div>
          <div class="control"><label for="physics-quality">模拟质量</label><input id="physics-quality" type="range" min="1" max="4" step="1" value="2"><span class="value" data-for="physics-quality">2</span></div>
          <button id="reset-physics" class="command" style="width:100%;margin-top:9px" disabled><i data-lucide="refresh-cw"></i>重置物理状态</button>
        </section>
        <section class="section">
          <div class="section-head"><h2>显示</h2></div>
          <div class="switch-row"><span>模型阴影</span><label class="switch"><input id="cast-shadow" type="checkbox" checked><span></span></label></div>
          <div class="switch-row"><span>地面</span><label class="switch"><input id="show-ground" type="checkbox" checked><span></span></label></div>
          <div class="switch-row"><span>参考网格</span><label class="switch"><input id="show-grid" type="checkbox"><span></span></label></div>
          <div class="switch-row"><span>线框模式</span><label class="switch"><input id="wireframe" type="checkbox"><span></span></label></div>
          <div class="switch-row"><span>抗锯齿轮廓</span><label class="switch"><input id="show-outline" type="checkbox" checked><span></span></label></div>
        </section>
      </div>

      <div class="panel" id="morph-panel">
        <section class="section">
          <div class="section-head"><h2>快捷表情</h2><span class="readout" id="expression-status">自然</span></div>
          <div class="preset-grid"><button class="command expression-preset" data-expression="smile">微笑</button><button class="command expression-preset" data-expression="blink">闭眼</button><button class="command expression-preset" data-expression="surprise">惊讶</button><button class="command expression-preset" data-expression="sad">难过</button><button class="command expression-preset" data-expression="wink-left">左眨眼</button><button class="command expression-preset" data-expression="wink-right">右眨眼</button></div>
          <div class="switch-row" style="margin-top:8px"><span>视线跟随相机</span><label class="switch"><input id="eye-tracking" type="checkbox"><span></span></label></div>
          <div class="control"><label for="eye-strength">视线强度</label><input id="eye-strength" type="range" min="0" max="1" step="0.05" value="0.65"><span class="value" data-for="eye-strength">65%</span></div>
          <div class="switch-row"><span>自动眨眼</span><label class="switch"><input id="auto-blink" type="checkbox"><span></span></label></div>
        </section>
        <section class="section">
          <div class="section-head"><h2>表情与形变</h2><span class="readout" id="morph-count">0 项</span></div>
          <input id="morph-search" class="search" type="search" placeholder="筛选名称" autocomplete="off">
          <div id="morph-list"><div class="empty">模型载入后显示</div></div>
        </section>
        <section class="section"><button id="clear-morphs" class="command" style="width:100%"><i data-lucide="refresh-cw"></i>全部归零</button></section>
      </div>
    </div>
  </aside>
</main>

<script src="/vendor/lucide/lucide.min.js"></script>
<script type="module">
  import * as THREE from 'three';
  import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
  import { TransformControls } from 'three/addons/controls/TransformControls.js';
  import { MMDLoader } from 'three/addons/loaders/MMDLoader.js';
  import { RGBELoader } from 'three/addons/loaders/RGBELoader.js';
  import { MMDAnimationHelper } from 'three/addons/animation/MMDAnimationHelper.js';
  import { CCDIKSolver } from 'three/addons/animation/CCDIKSolver.js';
  import { OutlineEffect } from 'three/addons/effects/OutlineEffect.js';
  import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
  import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
  import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
  import { BokehPass } from 'three/addons/postprocessing/BokehPass.js';
  import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

  const $ = (id) => document.getElementById(id);
  const viewport = $('viewport');
  const inspector = $('inspector');
  const scene = new THREE.Scene();
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  viewport.prepend(renderer.domElement);

  const camera = new THREE.PerspectiveCamera(39.6, 1, 0.02, 2000);
  camera.setFocalLength(50);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.065;
  controls.screenSpacePanning = true;
  controls.minPolarAngle = THREE.MathUtils.degToRad(8);
  controls.maxPolarAngle = THREE.MathUtils.degToRad(170);
  const transformControls = new TransformControls(camera, renderer.domElement);
  transformControls.setMode('rotate');
  transformControls.setSpace('local');
  transformControls.setSize(1);
  scene.add(transformControls.getHelper());

  const root = new THREE.Group();
  scene.add(root);
  const accessoryRoot = new THREE.Group();
  scene.add(accessoryRoot);
  let mesh = null;
  let modelCatalog = [];
  let selectedAccessoryKey = null;
  let accessorySerial = 0;
  const accessories = new Map();
  let skeletonHelper = null;
  let selectedPoseObject = null;
  let activeIk = null;
  let gizmoInteraction = false;
  let modelHeight = 20;
  let modelBaseY = 0;
  let outlineEnabled = true;
  let isCameraUiUpdate = false;
  let isPoseUiUpdate = false;
  let historySuspended = false;
  let poseGestureStart = null;
  let currentClip = null;
  let currentMotionKey = null;
  const motionClips = new Map();
  let animationHelper = null;
  let animationMixer = null;
  let animationAction = null;
  let playbackClock = 0;
  let cameraClip = null;
  let cameraAnimationHelper = null;
  let cameraAnimationMixer = null;
  let cameraAnimationAction = null;
  let preCameraVmdSnapshot = null;
  let audioElement = null;
  let audioObjectUrl = null;
  let nativeIkSolver = null;
  let animationPlaying = false;
  let physicsEnabled = false;
  let physicsInitialized = false;
  let ammoLoading = null;
  let hdrTexture = null;
  let hdrTexturePreset = null;
  let hdrRequestId = 0;
  let turntableRecording = null;
  let blinkClock = 0;
  let eyeBones = [];
  const restTransforms = new Map();
  const animationBaseTransforms = new Map();
  const poseOverrides = new Map();
  const poseHistory = [];
  const poseFuture = [];
  const favoriteBones = new Set((() => {
    try { const saved = JSON.parse(localStorage.getItem('mikuMmdFavorites') || '[]'); return Array.isArray(saved) ? saved : []; }
    catch { return []; }
  })());
  const ikHandles = new Map();
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const pointerStart = new THREE.Vector2();
  const effect = new OutlineEffect(renderer, { defaultThickness: 0.0025, defaultColor: [0.08, 0.1, 0.1], defaultAlpha: 0.7, defaultKeepAlive: true });
  const composer = new EffectComposer(renderer);
  const renderPass = new RenderPass(scene, camera);
  const bloomPass = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.35, 0.35, 0.86);
  bloomPass.enabled = false;
  const bokehPass = new BokehPass(scene, camera, { focus: 28, aperture: 0.00003, maxblur: 0.003 });
  bokehPass.enabled = false;
  const outputPass = new OutputPass();
  composer.addPass(renderPass);
  composer.addPass(bloomPass);
  composer.addPass(bokehPass);
  composer.addPass(outputPass);

  const boneMarker = new THREE.Mesh(
    new THREE.SphereGeometry(0.12, 18, 12),
    new THREE.MeshBasicMaterial({ color: 0x39e2bd, depthTest: false, transparent: true, opacity: 0.9 })
  );
  boneMarker.renderOrder = 1000;
  boneMarker.visible = false;
  scene.add(boneMarker);

  const hemi = new THREE.HemisphereLight(0xf4fbf8, 0x65706d, 1.15);
  scene.add(hemi);
  const keyLight = new THREE.DirectionalLight(0xfff4e4, 2.6);
  keyLight.castShadow = true;
  keyLight.shadow.mapSize.set(2048, 2048);
  keyLight.shadow.bias = -0.00015;
  keyLight.shadow.normalBias = 0.025;
  scene.add(keyLight, keyLight.target);
  const fillLight = new THREE.DirectionalLight(0xb9ddff, 0.55);
  scene.add(fillLight, fillLight.target);

  const groundMaterial = new THREE.ShadowMaterial({ color: 0x33403d, opacity: 0.2 });
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), groundMaterial);
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);
  const grid = new THREE.GridHelper(10, 20, 0x60706d, 0x9caaa7);
  grid.material.transparent = true;
  grid.material.opacity = 0.28;
  grid.visible = false;
  scene.add(grid);

  function resize() {
    const width = Math.max(1, viewport.clientWidth);
    const height = Math.max(1, viewport.clientHeight);
    renderer.setSize(width, height, false);
    composer.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(viewport);
  resize();

  function setStatus(text, state = '') {
    $('status').textContent = text;
    $('load-dot').className = state;
  }

  function showError(error) {
    console.error(error);
    $('loading').style.display = 'none';
    $('error-message').textContent = String(error?.message || error);
    $('error-box').style.display = 'block';
    setStatus('载入失败', 'error');
  }

  function updateCameraOptics() {
    const focal = Number($('focal').value);
    camera.filmGauge = Number($('sensor').value);
    camera.setFocalLength(focal);
    camera.updateProjectionMatrix();
    const fov = camera.getEffectiveFOV();
    document.querySelector('[data-for="focal"]').textContent = `${focal.toFixed(0)} mm`;
    document.querySelector('[data-for="sensor"]').textContent = `${$('sensor').value} mm`;
    $('fov-readout').textContent = `${fov.toFixed(1)}°`;
    $('camera-summary').textContent = `${focal.toFixed(0)} mm · ${fov.toFixed(1)}°`;
  }

  function setCameraFromUi() {
    if (!mesh || isCameraUiUpdate) return;
    const azimuth = THREE.MathUtils.degToRad(Number($('cam-azimuth').value));
    const elevation = THREE.MathUtils.degToRad(Number($('cam-elevation').value));
    const radius = Number($('cam-distance').value) * modelHeight;
    controls.target.set(0, modelBaseY + Number($('target-height').value) * modelHeight, 0);
    const cosElevation = Math.cos(elevation);
    camera.position.set(
      radius * Math.sin(azimuth) * cosElevation,
      controls.target.y + radius * Math.sin(elevation),
      radius * Math.cos(azimuth) * cosElevation
    );
    camera.lookAt(controls.target);
    controls.update();
    updateCameraLabels();
  }

  function updateCameraLabels() {
    document.querySelector('[data-for="cam-azimuth"]').textContent = `${Number($('cam-azimuth').value).toFixed(0)}°`;
    document.querySelector('[data-for="cam-elevation"]').textContent = `${Number($('cam-elevation').value).toFixed(0)}°`;
    document.querySelector('[data-for="cam-distance"]').textContent = `${Number($('cam-distance').value).toFixed(2)}×`;
    document.querySelector('[data-for="target-height"]').textContent = `${Math.round(Number($('target-height').value) * 100)}%`;
    $('distance-readout').textContent = `${camera.position.distanceTo(controls.target).toFixed(1)} u`;
  }

  function syncCameraUiFromOrbit() {
    if (!mesh) return;
    const offset = camera.position.clone().sub(controls.target);
    const radius = Math.max(0.001, offset.length());
    const azimuth = THREE.MathUtils.radToDeg(Math.atan2(offset.x, offset.z));
    const elevation = THREE.MathUtils.radToDeg(Math.asin(offset.y / radius));
    isCameraUiUpdate = true;
    $('cam-azimuth').value = String(THREE.MathUtils.clamp(azimuth, -180, 180));
    $('cam-elevation').value = String(THREE.MathUtils.clamp(elevation, -75, 75));
    $('cam-distance').value = String(THREE.MathUtils.clamp(radius / modelHeight, 0.55, 4));
    $('target-height').value = String(THREE.MathUtils.clamp((controls.target.y - modelBaseY) / modelHeight, 0.15, 0.9));
    isCameraUiUpdate = false;
    updateCameraLabels();
  }
  controls.addEventListener('change', syncCameraUiFromOrbit);

  function resetCamera() {
    if ($('camera-vmd-enabled')?.checked) { $('camera-vmd-enabled').checked = false; setCameraVmdEnabled(false); }
    $('cam-azimuth').value = '0';
    $('cam-elevation').value = '4';
    $('cam-distance').value = '1.4';
    $('target-height').value = '0.54';
    setCameraFromUi();
  }

  const cameraPresets = {
    full: { label: '正面全身', focal: 50, azimuth: 0, elevation: 4, distance: 1.4, targetHeight: 0.54 },
    bust: { label: '正面半身', focal: 70, azimuth: 0, elevation: 2, distance: 0.9, targetHeight: 0.67 },
    portrait: { label: '面部特写', focal: 85, azimuth: 0, elevation: 1, distance: 0.62, targetHeight: 0.82 },
    'three-quarter': { label: '三分之四视角', focal: 65, azimuth: 32, elevation: 3, distance: 1.08, targetHeight: 0.61 },
    profile: { label: '侧面视角', focal: 70, azimuth: 90, elevation: 2, distance: 1.0, targetHeight: 0.63 },
    'low-angle': { label: '低机位全身', focal: 42, azimuth: 22, elevation: -8, distance: 1.55, targetHeight: 0.46 }
  };

  function cameraSnapshot() {
    return {
      position: camera.position.toArray(), target: controls.target.toArray(),
      focal: Number($('focal').value), sensor: Number($('sensor').value)
    };
  }

  function applyCameraSnapshot(snapshot) {
    if (!snapshot || !Array.isArray(snapshot.position) || !Array.isArray(snapshot.target)) throw new Error('机位书签无效');
    camera.position.fromArray(snapshot.position); controls.target.fromArray(snapshot.target);
    $('focal').value = String(THREE.MathUtils.clamp(Number(snapshot.focal) || 50, 18, 120));
    $('sensor').value = String(THREE.MathUtils.clamp(Number(snapshot.sensor) || 36, 24, 50));
    updateCameraOptics(); camera.lookAt(controls.target); controls.update(); syncCameraUiFromOrbit();
  }

  function applyCameraPreset() {
    const preset = cameraPresets[$('camera-preset').value];
    if (!preset || !mesh) return;
    if ($('camera-vmd-enabled').checked) { $('camera-vmd-enabled').checked = false; setCameraVmdEnabled(false); }
    $('focal').value = String(preset.focal); $('cam-azimuth').value = String(preset.azimuth);
    $('cam-elevation').value = String(preset.elevation); $('cam-distance').value = String(preset.distance);
    $('target-height').value = String(preset.targetHeight);
    updateCameraOptics(); setCameraFromUi(); $('camera-preset-status').textContent = preset.label;
  }

  function updateLights() {
    const azimuth = THREE.MathUtils.degToRad(Number($('light-azimuth').value));
    const elevation = THREE.MathUtils.degToRad(Number($('light-elevation').value));
    const radius = modelHeight * 2.4;
    const centerY = modelBaseY + modelHeight * 0.5;
    keyLight.position.set(
      radius * Math.sin(azimuth) * Math.cos(elevation),
      centerY + radius * Math.sin(elevation),
      radius * Math.cos(azimuth) * Math.cos(elevation)
    );
    keyLight.target.position.set(0, centerY, 0);
    fillLight.position.set(-radius * 0.65, centerY + radius * 0.1, -radius * 0.3);
    fillLight.target.position.set(0, centerY, 0);
    keyLight.intensity = Number($('key-intensity').value);
    keyLight.color.set($('key-color').value);
    keyLight.shadow.radius = Number($('shadow-softness').value);
    hemi.intensity = Number($('ambient-intensity').value);
    fillLight.intensity = Number($('fill-intensity').value);
    fillLight.color.set($('fill-color').value);
    const shadowSpan = modelHeight * 0.72;
    Object.assign(keyLight.shadow.camera, { left: -shadowSpan, right: shadowSpan, top: shadowSpan, bottom: -shadowSpan, near: 0.05, far: modelHeight * 6 });
    keyLight.shadow.camera.updateProjectionMatrix();
    document.querySelector('[data-for="key-intensity"]').textContent = keyLight.intensity.toFixed(2);
    document.querySelector('[data-for="light-azimuth"]').textContent = `${$('light-azimuth').value}°`;
    document.querySelector('[data-for="light-elevation"]').textContent = `${$('light-elevation').value}°`;
    document.querySelector('[data-for="shadow-softness"]').textContent = Number($('shadow-softness').value).toFixed(1);
    document.querySelector('[data-for="ambient-intensity"]').textContent = hemi.intensity.toFixed(2);
    document.querySelector('[data-for="fill-intensity"]').textContent = fillLight.intensity.toFixed(2);
    $('key-direction').textContent = `${Number($('light-azimuth').value) >= 0 ? '右前' : '左前'} · ${$('light-elevation').value}°`;
  }

  function eachMaterial(callback) {
    if (!mesh) return;
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    materials.forEach((material) => material && callback(material));
  }

  function updateModelDisplay() {
    root.rotation.y = THREE.MathUtils.degToRad(Number($('model-yaw').value));
    const scale = Number($('model-scale').value);
    root.scale.setScalar(scale);
    mesh && mesh.traverse((object) => {
      if (object.isMesh) object.castShadow = $('cast-shadow').checked;
    });
    ground.visible = $('show-ground').checked;
    grid.visible = $('show-grid').checked;
    eachMaterial((material) => { material.wireframe = $('wireframe').checked; material.needsUpdate = true; });
    outlineEnabled = $('show-outline').checked;
    document.querySelector('[data-for="model-yaw"]').textContent = `${$('model-yaw').value}°`;
    document.querySelector('[data-for="model-scale"]').textContent = `${scale.toFixed(2)}×`;
    document.querySelector('[data-for="rotate-speed"]').textContent = `${Number($('rotate-speed').value).toFixed(1)}×`;
  }

  function cloneTransform(object) {
    return { position: object.position.clone(), quaternion: object.quaternion.clone(), scale: object.scale.clone() };
  }

  function rememberRestTransform(object) {
    restTransforms.set(object, cloneTransform(object));
    animationBaseTransforms.set(object, cloneTransform(object));
  }

  function boneAlias(name, index = 0) {
    if (!name) return `骨骼 ${index + 1}`;
    const side = name.includes('左') || /(^|[_.])L($|[_.])/i.test(name) ? '左' : name.includes('右') || /(^|[_.])R($|[_.])/i.test(name) ? '右' : '';
    const rules = [
      [/全ての親|mother|master/i, '全局控制'], [/センター|center/i, '身体中心'], [/グルーブ|groove/i, '重心'],
      [/上半身2/i, '上半身二段'], [/上半身/i, '上半身'], [/下半身/i, '下半身'], [/腰|waist/i, '腰部'],
      [/首|neck/i, '颈部'], [/頭|head/i, '头部'], [/両目/i, '双眼'], [/目|eye/i, '眼睛'],
      [/肩|shoulder/i, '肩膀'], [/腕|arm/i, '上臂'], [/ひじ|肘|elbow/i, '手肘'], [/手首|wrist/i, '手腕'],
      [/親指|thumb/i, '拇指'], [/人指|index/i, '食指'], [/中指|middle/i, '中指'], [/薬指|ring/i, '无名指'], [/小指|pinky/i, '小指'],
      [/足ＩＫ|足IK|leg.?ik/i, '足部 IK'], [/つま先ＩＫ|つま先IK|toe.?ik/i, '脚尖 IK'], [/足首|ankle/i, '脚踝'],
      [/ひざ|膝|knee/i, '膝盖'], [/足|leg/i, '大腿'], [/つま先|toe/i, '脚尖'], [/髪|hair/i, '头发'], [/スカート|skirt/i, '裙摆']
    ];
    const match = rules.find(([pattern]) => pattern.test(name));
    const alias = match ? match[1] : name;
    return side && !alias.startsWith(side) ? `${side}${alias}` : alias;
  }

  function boneCategory(name) {
    if (/髪|hair|スカート|skirt|胸|物理/i.test(name)) return 'physics';
    if (/頭|首|目|眼|舌|口|あご|顎|head|neck|eye/i.test(name)) return 'head';
    if (/肩|腕|ひじ|肘|手|指|shoulder|arm|elbow|wrist|finger|thumb/i.test(name)) return 'arm';
    if (/足|脚|ひざ|膝|つま先|leg|knee|ankle|toe/i.test(name)) return 'leg';
    if (/親|センター|グルーブ|上半身|下半身|腰|体|center|waist|spine|body/i.test(name)) return 'body';
    return 'other';
  }

  function objectKey(object) {
    if (object === root) return 'root';
    const index = mesh?.skeleton?.bones.indexOf(object) ?? -1;
    return index >= 0 ? String(index) : null;
  }

  function objectFromKey(key) {
    return key === 'root' ? root : mesh?.skeleton?.bones[Number(key)] || null;
  }

  function getPoseBase(object) {
    return currentClip && animationBaseTransforms.has(object) ? animationBaseTransforms.get(object) : restTransforms.get(object);
  }

  function maxJointAngle(object) {
    if (!$('joint-limits').checked || object === root) return Math.PI;
    const name = object.name || '';
    if (/目|eye/i.test(name)) return THREE.MathUtils.degToRad(30);
    if (/首|neck/i.test(name)) return THREE.MathUtils.degToRad(65);
    if (/頭|head/i.test(name)) return THREE.MathUtils.degToRad(85);
    if (/指|thumb|finger/i.test(name)) return THREE.MathUtils.degToRad(105);
    if (/ひじ|肘|ひざ|膝|elbow|knee/i.test(name)) return THREE.MathUtils.degToRad(165);
    if (/肩|腕|足|leg|arm|shoulder/i.test(name)) return THREE.MathUtils.degToRad(155);
    return Math.PI;
  }

  function clampOverride(object, override) {
    const limit = maxJointAngle(object);
    const identity = new THREE.Quaternion();
    if (identity.angleTo(override.quaternion) > limit) identity.rotateTowards(override.quaternion, limit);
    else identity.copy(override.quaternion);
    override.quaternion.copy(identity).normalize();
    return override;
  }

  function captureOverrideFromObject(object, applyMirror = true) {
    const base = getPoseBase(object);
    if (!base) return;
    const override = {
      position: object.position.clone().sub(base.position),
      quaternion: base.quaternion.clone().invert().multiply(object.quaternion).normalize()
    };
    clampOverride(object, override);
    poseOverrides.set(object, override);
    applyOverrideToObject(object);
    if (applyMirror && $('live-mirror').checked) mirrorObjectPose(object, false);
  }

  function applyOverrideToObject(object) {
    const base = getPoseBase(object);
    if (!base) return;
    const override = poseOverrides.get(object);
    object.position.copy(base.position);
    object.quaternion.copy(base.quaternion);
    if (override) {
      object.position.add(override.position);
      object.quaternion.multiply(override.quaternion);
    }
    object.updateMatrixWorld(true);
  }

  function applyPoseOverrides() {
    for (const object of poseOverrides.keys()) applyOverrideToObject(object);
  }

  function poseDocument() {
    const bones = [];
    for (const [object, override] of poseOverrides) {
      const key = objectKey(object);
      if (key === null) continue;
      bones.push({ key, name: object === root ? 'root' : object.name, position: override.position.toArray(), quaternion: override.quaternion.toArray() });
    }
    const morphs = {};
    if (mesh?.morphTargetDictionary) {
      for (const [name, index] of Object.entries(mesh.morphTargetDictionary)) {
        const value = mesh.morphTargetInfluences[index] || 0;
        if (Math.abs(value) > 0.0001) morphs[name] = value;
      }
    }
    return { format: 'miku-mmd-pose', version: 2, model: $('model-name').textContent, bones, morphs };
  }

  function poseSignature(documentObject = poseDocument()) {
    return JSON.stringify(documentObject);
  }

  function applyPoseDocument(documentObject) {
    if (!documentObject || documentObject.format !== 'miku-mmd-pose' || !Array.isArray(documentObject.bones)) throw new Error('不是有效的 Miku MMD 姿势文件');
    historySuspended = true;
    poseOverrides.clear();
    for (const item of documentObject.bones) {
      let object = objectFromKey(item.key);
      if ((!object || (item.name && item.name !== object.name)) && item.name && item.name !== 'root') object = mesh.skeleton.bones.find((bone) => bone.name === item.name);
      if (!object || !Array.isArray(item.position) || !Array.isArray(item.quaternion)) continue;
      poseOverrides.set(object, clampOverride(object, { position: new THREE.Vector3().fromArray(item.position), quaternion: new THREE.Quaternion().fromArray(item.quaternion) }));
    }
    if (mesh?.morphTargetDictionary) {
      mesh.morphTargetInfluences.fill(0);
      for (const [name, value] of Object.entries(documentObject.morphs || {})) {
        const index = mesh.morphTargetDictionary[name];
        if (index !== undefined) mesh.morphTargetInfluences[index] = THREE.MathUtils.clamp(Number(value), 0, 1);
      }
      syncMorphControls();
    }
    applyPoseOverrides();
    syncPoseUiFromObject();
    historySuspended = false;
  }

  function beginPoseGesture() {
    if (!mesh || historySuspended || poseGestureStart) return;
    poseGestureStart = poseDocument();
  }

  function endPoseGesture(label = '姿势调整') {
    if (!poseGestureStart || historySuspended) { poseGestureStart = null; return; }
    const after = poseDocument();
    if (poseSignature(poseGestureStart) !== poseSignature(after)) {
      poseHistory.push({ before: poseGestureStart, after, label });
      if (poseHistory.length > 80) poseHistory.shift();
      poseFuture.length = 0;
      updateHistoryUi();
    }
    poseGestureStart = null;
  }

  function updateHistoryUi() {
    $('undo-pose').disabled = poseHistory.length === 0;
    $('redo-pose').disabled = poseFuture.length === 0;
    $('history-status').textContent = `${poseHistory.length} 步`;
  }

  function undoPose() {
    const entry = poseHistory.pop();
    if (!entry) return;
    poseFuture.push(entry);
    applyPoseDocument(entry.before);
    updateHistoryUi();
  }

  function redoPose() {
    const entry = poseFuture.pop();
    if (!entry) return;
    poseHistory.push(entry);
    applyPoseDocument(entry.after);
    updateHistoryUi();
  }

  function rebuildBoneSelect(preferredValue = objectKey(selectedPoseObject) || 'root') {
    const select = $('bone-select');
    const category = $('bone-category').value;
    select.replaceChildren();
    const addOption = (value, label) => {
      const option = document.createElement('option'); option.value = value; option.textContent = label; select.append(option);
    };
    if (category === 'all' || category === 'body') addOption('root', '模型整体 / 根节点');
    (mesh?.skeleton?.bones || []).forEach((bone, index) => {
      if (category === 'favorite' && !favoriteBones.has(bone.name)) return;
      if (!['all', 'favorite'].includes(category) && boneCategory(bone.name) !== category) return;
      const alias = boneAlias(bone.name, index);
      addOption(String(index), alias === bone.name ? alias : `${alias} / ${bone.name}`);
    });
    if (!select.options.length) addOption('', '此分类暂无骨骼');
    if ([...select.options].some((option) => option.value === preferredValue)) select.value = preferredValue;
    else select.value = select.options[0].value;
  }

  function createBoneControls() {
    restTransforms.clear(); animationBaseTransforms.clear(); poseOverrides.clear();
    rememberRestTransform(root);
    const bones = mesh?.skeleton?.bones || [];
    bones.forEach(rememberRestTransform);
    $('bone-count').textContent = `${bones.length} 根`;
    const positionRange = Math.max(1, modelHeight * 0.25);
    ['pose-pos-x', 'pose-pos-y', 'pose-pos-z'].forEach((id) => {
      $(id).min = String(-positionRange); $(id).max = String(positionRange); $(id).step = String(Math.max(0.005, modelHeight / 2000));
    });
    if (skeletonHelper) scene.remove(skeletonHelper);
    skeletonHelper = new THREE.SkeletonHelper(mesh);
    skeletonHelper.visible = $('show-skeleton').checked;
    skeletonHelper.material.transparent = true;
    skeletonHelper.material.opacity = 0.7;
    skeletonHelper.material.depthTest = false;
    scene.add(skeletonHelper);
    rebuildBoneSelect('root');
    createIkHandles();
    selectPoseObject(root, 'root');
    updateHistoryUi();
  }

  function selectPoseObject(object, selectValue) {
    if (!object || !restTransforms.has(object)) return;
    deactivateIkHandle(true);
    selectedPoseObject = object;
    const category = $('bone-category').value;
    if (![...$('bone-select').options].some((option) => option.value === selectValue)) {
      $('bone-category').value = 'all'; rebuildBoneSelect(selectValue);
    } else $('bone-select').value = selectValue;
    transformControls.attach(object);
    const name = object === root ? '模型整体' : boneAlias(object.name, Number(selectValue));
    $('pose-selection-name').textContent = name;
    $('pose-selection').classList.add('visible');
    boneMarker.visible = object !== root;
    $('favorite-bone').disabled = object === root;
    $('favorite-bone').innerHTML = `<i data-lucide="star"></i>${object !== root && favoriteBones.has(object.name) ? '取消收藏' : '收藏当前骨骼'}`;
    window.lucide?.createIcons({ attrs: { 'stroke-width': 1.8 } });
    syncPoseUiFromObject();
  }

  function clearPoseSelection() {
    deactivateIkHandle(true);
    selectedPoseObject = null;
    transformControls.detach();
    boneMarker.visible = false;
    $('bone-label').style.display = 'none';
    $('pose-selection').classList.remove('visible');
    $('pose-selection-name').textContent = '--';
    $('bone-select').selectedIndex = -1;
    $('favorite-bone').disabled = true;
    $('favorite-bone').innerHTML = '<i data-lucide="star"></i>收藏当前骨骼';
    window.lucide?.createIcons({ attrs: { 'stroke-width': 1.8 } });
  }

  function syncPoseUiFromObject() {
    if (!selectedPoseObject) return;
    const override = poseOverrides.get(selectedPoseObject) || { position: new THREE.Vector3(), quaternion: new THREE.Quaternion() };
    isPoseUiUpdate = true;
    const deltaEuler = new THREE.Euler().setFromQuaternion(override.quaternion, 'XYZ');
    const rotationValues = [deltaEuler.x, deltaEuler.y, deltaEuler.z].map((value) => THREE.MathUtils.radToDeg(value));
    const positionValues = override.position.toArray();
    ['x', 'y', 'z'].forEach((axis, index) => {
      const rotation = THREE.MathUtils.clamp(rotationValues[index], -180, 180);
      $(`pose-rot-${axis}`).value = String(rotation);
      document.querySelector(`[data-for="pose-rot-${axis}"]`).textContent = `${rotation.toFixed(0)}°`;
      $(`pose-pos-${axis}`).value = String(positionValues[index]);
      document.querySelector(`[data-for="pose-pos-${axis}"]`).textContent = positionValues[index].toFixed(2);
    });
    isPoseUiUpdate = false;
  }

  function applyPoseRotation() {
    if (!selectedPoseObject || isPoseUiUpdate) return;
    const existing = poseOverrides.get(selectedPoseObject) || { position: new THREE.Vector3(), quaternion: new THREE.Quaternion() };
    existing.quaternion.setFromEuler(new THREE.Euler(
      THREE.MathUtils.degToRad(Number($('pose-rot-x').value)), THREE.MathUtils.degToRad(Number($('pose-rot-y').value)), THREE.MathUtils.degToRad(Number($('pose-rot-z').value)), 'XYZ'
    ));
    poseOverrides.set(selectedPoseObject, clampOverride(selectedPoseObject, existing));
    applyOverrideToObject(selectedPoseObject);
    if ($('live-mirror').checked) mirrorObjectPose(selectedPoseObject, false);
    syncPoseUiFromObject();
  }

  function applyPosePosition() {
    if (!selectedPoseObject || isPoseUiUpdate) return;
    const existing = poseOverrides.get(selectedPoseObject) || { position: new THREE.Vector3(), quaternion: new THREE.Quaternion() };
    existing.position.set(Number($('pose-pos-x').value), Number($('pose-pos-y').value), Number($('pose-pos-z').value));
    poseOverrides.set(selectedPoseObject, existing);
    applyOverrideToObject(selectedPoseObject);
    if ($('live-mirror').checked) mirrorObjectPose(selectedPoseObject, false);
    syncPoseUiFromObject();
  }

  function setPoseMode(mode) {
    if (activeIk && mode !== 'translate') deactivateIkHandle(true);
    transformControls.setMode(mode);
    document.querySelectorAll('#pose-mode .segment').forEach((button) => button.classList.toggle('active', button.dataset.mode === mode));
    updatePoseModeReadout();
  }

  function setPoseSpace(space) {
    transformControls.setSpace(space);
    document.querySelectorAll('#pose-space .segment').forEach((button) => button.classList.toggle('active', button.dataset.space === space));
    updatePoseModeReadout();
  }

  function updatePoseModeReadout() {
    $('pose-mode-readout').textContent = `${transformControls.mode === 'rotate' ? '旋转' : '移动'} · ${transformControls.space === 'local' ? '本地' : '世界'}`;
  }

  function counterpartBone(object) {
    if (!object || object === root) return null;
    const name = object.name || '';
    const candidates = name.includes('左') ? [name.replace('左', '右')] : name.includes('右') ? [name.replace('右', '左')] : [
      name.replace(/\.L\b/i, '.R'), name.replace(/\.R\b/i, '.L'), name.replace(/_L\b/i, '_R'), name.replace(/_R\b/i, '_L'),
      name.replace(/left/i, 'right'), name.replace(/right/i, 'left')
    ];
    return mesh.skeleton.bones.find((bone) => candidates.includes(bone.name)) || null;
  }

  function mirroredOverride(override) {
    return {
      position: new THREE.Vector3(-override.position.x, override.position.y, override.position.z),
      quaternion: new THREE.Quaternion(override.quaternion.x, -override.quaternion.y, -override.quaternion.z, override.quaternion.w).normalize()
    };
  }

  function mirrorObjectPose(object, selectTarget = true) {
    const target = counterpartBone(object);
    if (!target) { $('mirror-status').textContent = '无对应骨骼'; return false; }
    const sourceOverride = poseOverrides.get(object) || { position: new THREE.Vector3(), quaternion: new THREE.Quaternion() };
    poseOverrides.set(target, clampOverride(target, mirroredOverride(sourceOverride)));
    applyOverrideToObject(target);
    $('mirror-status').textContent = `${boneAlias(object.name)} → ${boneAlias(target.name)}`;
    if (selectTarget) selectPoseObject(target, String(mesh.skeleton.bones.indexOf(target)));
    return true;
  }

  function swapSidePoses() {
    const visited = new Set();
    for (const bone of mesh.skeleton.bones) {
      if (visited.has(bone)) continue;
      const other = counterpartBone(bone);
      if (!other) continue;
      visited.add(bone); visited.add(other);
      const a = poseOverrides.get(bone); const b = poseOverrides.get(other);
      if (b) poseOverrides.set(bone, clampOverride(bone, mirroredOverride(b))); else poseOverrides.delete(bone);
      if (a) poseOverrides.set(other, clampOverride(other, mirroredOverride(a))); else poseOverrides.delete(other);
      applyOverrideToObject(bone); applyOverrideToObject(other);
    }
  }

  function resetSelectedPose() {
    if (!selectedPoseObject) return;
    beginPoseGesture(); poseOverrides.delete(selectedPoseObject); applyOverrideToObject(selectedPoseObject);
    if ($('live-mirror').checked) { const other = counterpartBone(selectedPoseObject); if (other) { poseOverrides.delete(other); applyOverrideToObject(other); } }
    syncPoseUiFromObject(); endPoseGesture('当前复位');
  }

  function resetAllPose() {
    beginPoseGesture(); poseOverrides.clear();
    for (const object of restTransforms.keys()) applyOverrideToObject(object);
    $('model-yaw').value = '0'; $('auto-rotate').checked = false; updateModelDisplay(); syncPoseUiFromObject(); endPoseGesture('全身复位');
  }

  const handPresetLabels = { open: '张开', relaxed: '放松', fist: '握拳', point: '指向', peace: '剪刀手' };

  function boneSide(name) {
    if (/左|(?:^|[_.])L(?:$|[_.])|left/i.test(name)) return 'left';
    if (/右|(?:^|[_.])R(?:$|[_.])|right/i.test(name)) return 'right';
    return '';
  }

  function setBoneEuler(bone, degrees) {
    if (!bone) return false;
    const existing = poseOverrides.get(bone) || { position: new THREE.Vector3(), quaternion: new THREE.Quaternion() };
    existing.quaternion.setFromEuler(new THREE.Euler(...degrees.map(THREE.MathUtils.degToRad), 'XYZ'));
    poseOverrides.set(bone, clampOverride(bone, existing)); applyOverrideToObject(bone); return true;
  }

  function findPresetBone(patterns, side = '') {
    return (mesh?.skeleton?.bones || []).find((bone) => (!side || boneSide(bone.name || '') === side) && patterns.some((pattern) => pattern.test(bone.name || ''))) || null;
  }

  function applyHandPreset(key) {
    if (!mesh) return;
    const selectedSide = $('hand-preset-side').value;
    const sides = selectedSide === 'both' ? ['left', 'right'] : [selectedSide];
    beginPoseGesture(); let matched = 0;
    for (const bone of mesh.skeleton.bones) {
      const side = boneSide(bone.name || '');
      if (!sides.includes(side) || /指先|finger.?tip/i.test(bone.name || '') || !/親指|人指|中指|薬指|小指|thumb|index|middle|ring|pinky|finger/i.test(bone.name || '')) continue;
      const isThumb = /親指|thumb/i.test(bone.name || '');
      const isIndex = /人指|index/i.test(bone.name || '');
      const isMiddle = /中指|middle/i.test(bone.name || '');
      let bend = 0;
      if (key === 'relaxed') bend = isThumb ? 14 : isIndex ? 18 : isMiddle ? 24 : 30;
      if (key === 'fist') bend = isThumb ? 32 : 58;
      if (key === 'point') bend = isIndex ? 0 : isThumb ? 28 : 58;
      if (key === 'peace') bend = isIndex || isMiddle ? 0 : isThumb ? 28 : 58;
      if (key === 'open') poseOverrides.delete(bone);
      else setBoneEuler(bone, [0, 0, (side === 'left' ? -1 : 1) * bend]);
      applyOverrideToObject(bone); matched += 1;
    }
    syncPoseUiFromObject(); endPoseGesture(`手势：${handPresetLabels[key] || key}`);
    $('pose-preset-status').textContent = matched ? `${handPresetLabels[key]} · ${selectedSide === 'both' ? '双手' : selectedSide === 'left' ? '左手' : '右手'}` : '未匹配手指骨骼';
  }

  const bodyPresetLabels = { neutral: '基础姿态', 'arms-down': '自然垂臂', relaxed: '轻松站姿', 'wave-left': '左手挥手', 'wave-right': '右手挥手', contrapposto: '重心站姿' };

  function applyBodyPreset(key) {
    if (!mesh) return;
    beginPoseGesture(); poseOverrides.clear(); let matched = 0;
    const apply = (patterns, side, degrees) => { if (setBoneEuler(findPresetBone(patterns, side), degrees)) matched += 1; };
    if (key === 'arms-down' || key === 'relaxed' || key === 'contrapposto') {
      apply([/^左腕$/, /^arm(?:[_.]?L)?$/i], 'left', [0, 0, 42]); apply([/^右腕$/, /^arm(?:[_.]?R)?$/i], 'right', [0, 0, -42]);
      apply([/^左ひじ$/, /^elbow(?:[_.]?L)?$/i], 'left', [0, -8, 8]); apply([/^右ひじ$/, /^elbow(?:[_.]?R)?$/i], 'right', [0, 8, -8]);
    }
    if (key === 'relaxed') {
      apply([/^上半身$/, /^spine$/i], '', [0, 4, 0]); apply([/^頭$/, /^head$/i], '', [2, -5, 0]);
      apply([/^左足$/, /^leg(?:[_.]?L)?$/i], 'left', [0, 0, 4]); apply([/^右足$/, /^leg(?:[_.]?R)?$/i], 'right', [0, 0, -4]);
    }
    if (key === 'wave-left' || key === 'wave-right') {
      const side = key.endsWith('left') ? 'left' : 'right'; const sign = side === 'left' ? 1 : -1;
      apply([new RegExp(`^${side === 'left' ? '左' : '右'}腕$`), /^arm/i], side, [0, 0, 80 * sign]);
      apply([new RegExp(`^${side === 'left' ? '左' : '右'}ひじ$`), /^elbow/i], side, [0, 0, 80 * sign]);
      apply([new RegExp(`^${side === 'left' ? '左' : '右'}手首$`), /^wrist/i], side, [0, 18 * sign, 12 * sign]);
      const other = side === 'left' ? 'right' : 'left'; apply([new RegExp(`^${other === 'left' ? '左' : '右'}腕$`), /^arm/i], other, [0, 0, other === 'left' ? 38 : -38]);
    }
    if (key === 'contrapposto') {
      apply([/^下半身$/, /lower.?body/i], '', [0, 0, 6]); apply([/^上半身$/, /^spine$/i], '', [0, 0, -5]);
      apply([/^右ひざ$/, /^knee(?:[_.]?R)?$/i], 'right', [8, 0, 0]);
    }
    for (const object of restTransforms.keys()) applyOverrideToObject(object);
    syncPoseUiFromObject(); endPoseGesture(`姿势模板：${bodyPresetLabels[key] || key}`);
    $('pose-preset-status').textContent = key === 'neutral' || matched ? bodyPresetLabels[key] : '未匹配标准骨骼';
  }

  function selectBoneFromSurface(event) {
    if (!mesh?.skeleton || gizmoInteraction) return;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const face = raycaster.intersectObject(mesh, false)[0]?.face;
    if (!face) { clearPoseSelection(); return; }
    if (!$('surface-select').checked || activeIk) return;
    const skinIndex = mesh.geometry.getAttribute('skinIndex'); const skinWeight = mesh.geometry.getAttribute('skinWeight');
    if (!skinIndex || !skinWeight) return;
    const totals = new Map(); const getters = ['getX', 'getY', 'getZ', 'getW'];
    for (const vertex of [face.a, face.b, face.c]) getters.forEach((getter) => {
      const weight = skinWeight[getter](vertex); const index = Math.round(skinIndex[getter](vertex));
      if (weight > 0.001) totals.set(index, (totals.get(index) || 0) + weight);
    });
    let selectedIndex = -1; let selectedWeight = -1;
    totals.forEach((weight, index) => { if (weight > selectedWeight && mesh.skeleton.bones[index]) { selectedIndex = index; selectedWeight = weight; } });
    if (selectedIndex >= 0) selectPoseObject(mesh.skeleton.bones[selectedIndex], String(selectedIndex));
  }

  function findBone(patterns) {
    return mesh?.skeleton?.bones.find((bone) => patterns.some((pattern) => pattern.test(bone.name || ''))) || null;
  }

  function createIkHandles() {
    for (const handle of ikHandles.values()) scene.remove(handle.target);
    ikHandles.clear();
    const select = $('ik-handle'); select.replaceChildren();
    const bones = mesh.skeleton.bones;
    nativeIkSolver = new CCDIKSolver(mesh, mesh.geometry.userData.MMD?.iks || []);

    const definitions = [
      ['left-hand', '左手', [/左.*(手首|wrist)/i], 3, false], ['right-hand', '右手', [/右.*(手首|wrist)/i], 3, false],
      ['left-foot', '左脚', [/左.*(足ＩＫ|足IK|leg.?ik)/i, /左.*(足首|ankle)/i], 2, true],
      ['right-foot', '右脚', [/右.*(足ＩＫ|足IK|leg.?ik)/i, /右.*(足首|ankle)/i], 2, true],
      ['head', '头部', [/(^|[^左右])(頭|head)$/i, /頭|head/i], 2, false],
      ['waist', '腰部/重心', [/(センター|center|腰|waist)/i], 0, true]
    ];
    for (const [key, label, patterns, linkCount, preferDirect] of definitions) {
      const effector = findBone(patterns);
      if (!effector) continue;
      const target = new THREE.Mesh(
        new THREE.SphereGeometry(Math.max(0.08, modelHeight * 0.012), 18, 12),
        new THREE.MeshBasicMaterial({ color: 0xf0b74f, depthTest: false, transparent: true, opacity: 0.88 })
      );
      target.name = `IK ${label}`; target.visible = false; target.renderOrder = 1001; scene.add(target);
      const effectorIndex = bones.indexOf(effector);
      const isExistingIk = /ＩＫ|IK/i.test(effector.name || '');
      const direct = preferDirect && (isExistingIk || linkCount === 0);
      const links = [];
      if (!direct) {
        let parent = effector.parent;
        while (parent?.isBone && links.length < linkCount) {
          const index = bones.indexOf(parent);
          if (index < 0) break;
          links.push({ index }); parent = parent.parent;
        }
      }
      let solver = null;
      if (!direct && links.length) {
        const fakeBones = bones.slice(); const targetIndex = fakeBones.push(target) - 1;
        solver = new CCDIKSolver({ skeleton: { bones: fakeBones } }, [{ target: targetIndex, effector: effectorIndex, links, iteration: 10, maxAngle: 0.45 }]);
      }
      ikHandles.set(key, { key, label, effector, target, links, solver, direct });
      const option = document.createElement('option'); option.value = key; option.textContent = label; select.append(option);
    }
    $('ik-status').textContent = ikHandles.size ? '可用' : '未发现控制链';
  }

  function activateIkHandle(key = $('ik-handle').value) {
    const handle = ikHandles.get(key);
    if (!handle) { $('ik-enabled').checked = false; return; }
    for (const item of ikHandles.values()) item.target.visible = false;
    activeIk = handle;
    handle.target.visible = true;
    handle.target.position.copy(handle.effector.getWorldPosition(new THREE.Vector3()));
    transformControls.attach(handle.target);
    setPoseMode('translate'); setPoseSpace('world');
    $('ik-enabled').checked = true;
    $('ik-status').textContent = `${handle.label}控制中`;
    $('pose-selection-name').textContent = `IK ${handle.label}`;
    $('pose-selection').classList.add('visible');
    boneMarker.visible = false;
  }

  function deactivateIkHandle(uncheck = true) {
    if (activeIk) activeIk.target.visible = false;
    activeIk = null;
    if (uncheck) $('ik-enabled').checked = false;
    if (selectedPoseObject && restTransforms.has(selectedPoseObject)) transformControls.attach(selectedPoseObject);
    $('ik-status').textContent = ikHandles.size ? '可用' : '关闭';
  }

  function solveActiveIk() {
    const handle = activeIk;
    if (!handle) return;
    root.updateMatrixWorld(true); handle.target.updateMatrixWorld(true);
    if (handle.direct) {
      const world = handle.target.getWorldPosition(new THREE.Vector3());
      const parent = handle.effector.parent;
      handle.effector.position.copy(parent ? parent.worldToLocal(world.clone()) : world);
      captureOverrideFromObject(handle.effector, false);
      nativeIkSolver?.update();
      for (const bone of mesh.skeleton.bones) {
        const base = getPoseBase(bone); if (!base) continue;
        if (base.position.distanceToSquared(bone.position) > 1e-8 || base.quaternion.angleTo(bone.quaternion) > 1e-5) captureOverrideFromObject(bone, false);
      }
    } else if (handle.solver) {
      handle.solver.iks[0].iteration = Number($('ik-iterations').value);
      handle.solver.update();
      for (const link of handle.links) {
        const bone = mesh.skeleton.bones[link.index];
        captureOverrideFromObject(bone, false);
        if ($('live-mirror').checked) mirrorObjectPose(bone, false);
      }
    }
    mesh.updateMatrixWorld(true);
  }

  function createMorphControls() {
    const list = $('morph-list');
    list.replaceChildren();
    const dictionary = mesh?.morphTargetDictionary || {};
    const entries = Object.entries(dictionary).sort((a, b) => a[1] - b[1]);
    $('morph-count').textContent = `${entries.length} 项`;
    if (!entries.length) {
      list.innerHTML = '<div class="empty">未发现可调形变</div>';
      return;
    }
    for (const [name, index] of entries) {
      const row = document.createElement('div');
      row.className = 'control morph-control';
      row.dataset.name = name.toLowerCase();
      const label = document.createElement('label');
      label.title = name;
      label.textContent = name;
      const input = document.createElement('input');
      input.type = 'range'; input.min = '0'; input.max = '1'; input.step = '0.01'; input.value = '0';
      const value = document.createElement('span');
      value.className = 'value'; value.textContent = '0%';
      input.addEventListener('pointerdown', beginPoseGesture);
      input.addEventListener('input', () => {
        mesh.morphTargetInfluences[index] = Number(input.value);
        value.textContent = `${Math.round(Number(input.value) * 100)}%`;
      });
      input.addEventListener('change', () => endPoseGesture('表情调整'));
      row.append(label, input, value);
      list.append(row);
    }
  }

  function syncMorphControls() {
    if (!mesh?.morphTargetDictionary) return;
    document.querySelectorAll('.morph-control').forEach((row) => {
      const name = row.querySelector('label').textContent;
      const index = mesh.morphTargetDictionary[name];
      const value = index === undefined ? 0 : mesh.morphTargetInfluences[index] || 0;
      row.querySelector('input').value = String(value);
      row.querySelector('.value').textContent = `${Math.round(value * 100)}%`;
    });
  }

  function setMorphsByPatterns(patterns, value) {
    if (!mesh?.morphTargetDictionary) return false;
    const patternList = Array.isArray(patterns) ? patterns : [patterns];
    let changed = false;
    for (const [name, index] of Object.entries(mesh.morphTargetDictionary)) {
      if (patternList.some((pattern) => pattern.test(name))) {
        mesh.morphTargetInfluences[index] = value; changed = true;
      }
    }
    return changed;
  }

  const expressionPresets = {
    smile: { label: '微笑', entries: [[/笑|smile|にこ/i, 0.8], [/まばたき|blink/i, 0.18]] },
    blink: { label: '闭眼', entries: [[/まばたき|両目閉|blink/i, 1]] },
    surprise: { label: '惊讶', entries: [[/びっくり|驚|surprise/i, 0.9], [/あ|口開|mouth.?a/i, 0.45]] },
    sad: { label: '难过', entries: [[/困|悲|sad|眉/i, 0.75]] },
    'wink-left': { label: '左眨眼', entries: [[/ウィンク.*左|左.*閉|wink.*L/i, 1]] },
    'wink-right': { label: '右眨眼', entries: [[/ウィンク.*右|右.*閉|wink.*R/i, 1]] }
  };

  function applyExpressionPreset(key) {
    const preset = expressionPresets[key];
    if (!preset || !mesh?.morphTargetDictionary) return;
    beginPoseGesture();
    mesh.morphTargetInfluences.fill(0);
    let matched = false;
    for (const [patterns, value] of preset.entries) matched = setMorphsByPatterns(patterns, value) || matched;
    syncMorphControls();
    $('expression-status').textContent = matched ? preset.label : '模型无对应项';
    endPoseGesture(`表情：${preset.label}`);
  }

  function updateEyeTracking() {
    if (!$('eye-tracking').checked || !eyeBones.length) return;
    const strength = Number($('eye-strength').value);
    for (const bone of eyeBones) {
      const base = getPoseBase(bone) || restTransforms.get(bone);
      if (!base) continue;
      const bonePosition = bone.getWorldPosition(new THREE.Vector3());
      const targetLocal = root.worldToLocal(camera.getWorldPosition(new THREE.Vector3()));
      const boneLocal = root.worldToLocal(bonePosition.clone());
      const direction = targetLocal.sub(boneLocal);
      const yaw = THREE.MathUtils.clamp(Math.atan2(direction.x, Math.abs(direction.z)), -0.45, 0.45) * strength;
      const pitch = THREE.MathUtils.clamp(Math.atan2(direction.y, Math.hypot(direction.x, direction.z)), -0.3, 0.3) * strength;
      const eyeQuaternion = new THREE.Quaternion().setFromEuler(new THREE.Euler(-pitch, yaw, 0, 'YXZ'));
      bone.quaternion.copy(base.quaternion).multiply(eyeQuaternion);
    }
  }

  function prepareModel(loadedMesh) {
    mesh = loadedMesh;
    mesh.traverse((object) => {
      if (object.isMesh) { object.castShadow = true; object.receiveShadow = false; object.frustumCulled = false; }
    });
    root.add(mesh);
    mesh.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(mesh);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    modelHeight = Math.max(size.y, 0.1);
    modelBaseY = 0;
    mesh.position.x -= center.x;
    mesh.position.z -= center.z;
    mesh.position.y -= box.min.y;
    mesh.updateMatrixWorld(true);
    ground.scale.set(modelHeight * 5, modelHeight * 5, 1);
    ground.position.y = -modelHeight * 0.002;
    grid.scale.setScalar(modelHeight * 0.5);
    grid.position.y = ground.position.y + modelHeight * 0.0005;
    controls.minDistance = modelHeight * 0.2;
    controls.maxDistance = modelHeight * 8;
    camera.near = Math.max(modelHeight / 2000, 0.005);
    camera.far = modelHeight * 40;
    camera.updateProjectionMatrix();
    boneMarker.scale.setScalar(Math.max(0.5, modelHeight / 20));
    eyeBones = mesh.skeleton.bones.filter((bone) => /左目|右目|eye.?[LR]/i.test(bone.name || ''));
    $('model-size').textContent = `${size.x.toFixed(1)} × ${size.y.toFixed(1)} u`;
    createBoneControls();
    createMorphControls();
    resetCamera();
    updateLights();
    updateModelDisplay();
  }

  function formatTime(seconds) {
    const safe = Math.max(0, Number(seconds) || 0);
    const minutes = Math.floor(safe / 60);
    const remainder = safe - minutes * 60;
    return `${String(minutes).padStart(2, '0')}:${remainder.toFixed(3).padStart(6, '0')}`;
  }

  function playbackDuration() {
    const audioDuration = Number.isFinite(audioElement?.duration) ? Math.max(0, audioElement.duration + Number($('audio-delay').value)) : 0;
    return Math.max(currentClip?.duration || 0, $('camera-vmd-enabled').checked ? cameraClip?.duration || 0 : 0, audioDuration);
  }

  function playbackTime() {
    return playbackClock;
  }

  function hasPlaybackSource() {
    return Boolean(currentClip || ($('camera-vmd-enabled').checked && cameraClip) || audioElement);
  }

  function configureAnimationAction(action, clip) {
    if (!action || !clip) return;
    action.enabled = true;
    action.setLoop($('animation-loop').checked ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
    action.clampWhenFinished = !$('animation-loop').checked;
  }

  function setMotionActionsPaused(paused) {
    if (!animationMixer) return;
    for (const [key, clip] of motionClips) {
      const action = animationMixer.clipAction(clip);
      const contributes = key === currentMotionKey || action.getEffectiveWeight() > 0.0001;
      action.paused = paused || !contributes;
      if (!action.paused) action.play();
    }
  }

  function settleMotionBlend() {
    if (!animationMixer) return;
    for (const [key, clip] of motionClips) {
      const action = animationMixer.clipAction(clip);
      action.stopFading(); action.setEffectiveWeight(key === currentMotionKey ? 1 : 0);
      action.paused = !animationPlaying || key !== currentMotionKey;
    }
  }

  function updateAnimationUi() {
    const duration = playbackDuration();
    const time = Math.min(playbackTime(), duration || Infinity);
    $('animation-time').max = String(Math.max(duration, 0.001));
    if (!$('animation-time').matches(':active')) $('animation-time').value = String(time);
    $('animation-time-label').textContent = `${formatTime(time)} / ${formatTime(duration)}`;
    $('animation-status').textContent = hasPlaybackSource() ? (animationPlaying ? '播放中' : '已暂停') : '未载入';
    const enabled = hasPlaybackSource();
    ['animation-time', 'animation-play', 'animation-stop'].forEach((id) => $(id).disabled = !enabled);
    $('animation-remove').disabled = !currentClip;
    const state = animationPlaying ? 'playing' : 'paused';
    if ($('animation-play').dataset.state !== state) {
      $('animation-play').dataset.state = state;
      $('animation-play').innerHTML = animationPlaying ? '<i data-lucide="pause"></i>暂停' : '<i data-lucide="play"></i>播放';
      window.lucide?.createIcons({ attrs: { 'stroke-width': 1.8 } });
    }
  }

  function rebuildAnimationHelper(preserveTime = 0) {
    if (!mesh) return;
    if (animationHelper?.meshes.includes(mesh)) animationHelper.remove(mesh);
    animationHelper = null; animationMixer = null; animationAction = null;
    const clips = [...motionClips.values()];
    if (!clips.length && !physicsEnabled) {
      mesh.pose();
      for (const object of restTransforms.keys()) animationBaseTransforms.set(object, cloneTransform(object));
      applyPoseOverrides(); updateAnimationUi(); return;
    }
    animationHelper = new MMDAnimationHelper({ sync: false, pmxAnimation: true, resetPhysicsOnLoop: true });
    const params = {
      physics: physicsEnabled,
      warmup: physicsEnabled ? 12 : 0,
      maxStepNum: Number($('physics-quality').value),
      gravity: new THREE.Vector3(0, -98 * Number($('physics-gravity').value), 0)
    };
    if (clips.length) params.animation = clips;
    animationHelper.add(mesh, params);
    const objects = animationHelper.objects.get(mesh);
    animationMixer = objects?.mixer || null;
    animationAction = currentClip && animationMixer ? animationMixer.clipAction(currentClip) : null;
    if (animationMixer) {
      for (const [key, clip] of motionClips) {
        const action = animationMixer.clipAction(clip); configureAnimationAction(action, clip);
        const active = key === currentMotionKey;
        action.setEffectiveWeight(active ? 1 : 0); action.paused = !animationPlaying || !active;
      }
      animationMixer.setTime(Math.min(preserveTime, currentClip?.duration || 0));
    }
    animationHelper.enable('physics', physicsEnabled);
    animationHelper.update(0);
    for (const object of restTransforms.keys()) animationBaseTransforms.set(object, cloneTransform(object));
    applyPoseOverrides(); updateAnimationUi();
  }

  function rebuildMotionSelect() {
    const select = $('motion-select'); select.replaceChildren();
    if (!motionClips.size) { const option = document.createElement('option'); option.value = ''; option.textContent = '尚未添加动作'; select.append(option); return; }
    for (const [key, clip] of motionClips) {
      const option = document.createElement('option'); option.value = key; option.textContent = clip.userData?.fileName || clip.name || key; select.append(option);
    }
    if (currentMotionKey && motionClips.has(currentMotionKey)) select.value = currentMotionKey;
  }

  async function loadVmdFiles(files) {
    const list = [...files]; if (!list.length || !mesh) return;
    $('animation-status').textContent = '解析中';
    for (const file of list) {
      const url = URL.createObjectURL(file); const loader = new MMDLoader();
      try {
        const clip = await new Promise((resolve, reject) => loader.loadAnimation(url, mesh, resolve, undefined, reject));
        const baseKey = `${file.name}:${file.size}:${file.lastModified}`; let key = baseKey; let suffix = 2;
        while (motionClips.has(key)) key = `${baseKey}:${suffix++}`;
        clip.userData ||= {}; clip.userData.fileName = file.name; motionClips.set(key, clip);
        if (!currentClip) { currentMotionKey = key; currentClip = clip; }
      } catch (error) { console.error(error); $('animation-status').textContent = `${file.name} 失败`; }
      finally { URL.revokeObjectURL(url); }
    }
    rebuildMotionSelect(); animationPlaying = false; rebuildAnimationHelper(0);
    $('animation-file').textContent = currentClip?.userData?.fileName || '--'; setStatus('VMD 动作库已更新', 'ready');
  }

  function switchMotion(key, useFade = true) {
    const nextClip = motionClips.get(key); if (!nextClip || nextClip === currentClip) return;
    const previousAction = animationAction; currentMotionKey = key; currentClip = nextClip;
    if (!animationMixer) rebuildAnimationHelper(0);
    else {
      const nextAction = animationMixer.clipAction(nextClip); configureAnimationAction(nextAction, nextClip);
      nextAction.reset().setEffectiveWeight(1).play(); nextAction.paused = !animationPlaying;
      const fade = useFade ? Number($('animation-fade').value) : 0;
      if (previousAction && animationPlaying && fade > 0) { previousAction.paused = false; previousAction.crossFadeTo(nextAction, fade, true); }
      else for (const clip of motionClips.values()) animationMixer.clipAction(clip).stopFading().setEffectiveWeight(clip === nextClip ? 1 : 0);
      animationAction = nextAction;
    }
    playbackClock = 0; if (cameraAnimationMixer) { cameraAnimationMixer.setTime(0); cameraAnimationHelper?.update(0); }
    $('motion-select').value = key; $('animation-file').textContent = nextClip.userData?.fileName || nextClip.name; syncAudioToTime(0, animationPlaying); updateAnimationUi();
  }

  function toggleAnimationPlayback() {
    if (!hasPlaybackSource()) return;
    animationPlaying = !animationPlaying;
    const duration = playbackDuration(); if (playbackTime() >= duration - 0.01) setPlaybackTime(0);
    setMotionActionsPaused(!animationPlaying);
    if (cameraAnimationAction) { cameraAnimationAction.paused = !animationPlaying; if (animationPlaying && $('camera-vmd-enabled').checked) cameraAnimationAction.play(); }
    syncAudioToTime(playbackTime(), animationPlaying);
    updateAnimationUi();
  }

  function stopAnimation() {
    animationPlaying = false; settleMotionBlend(); if (cameraAnimationAction) cameraAnimationAction.paused = true; setPlaybackTime(0);
    audioElement?.pause(); applyPoseOverrides(); updateAnimationUi();
  }

  function removeAnimation() {
    if (!currentMotionKey) return;
    motionClips.delete(currentMotionKey); const next = motionClips.entries().next().value;
    currentMotionKey = next?.[0] || null; currentClip = next?.[1] || null; animationPlaying = false;
    $('animation-file').textContent = currentClip?.userData?.fileName || '--'; rebuildMotionSelect(); rebuildAnimationHelper(0); updateAnimationUi();
  }

  function setPlaybackTime(time) {
    const safe = Math.max(0, Number(time) || 0);
    playbackClock = safe;
    if (animationMixer) { animationMixer.setTime(safe); animationHelper?.update(0); }
    if (cameraAnimationMixer) { cameraAnimationMixer.setTime(safe); cameraAnimationHelper?.update(0); }
    syncAudioToTime(safe, animationPlaying); applyPoseOverrides();
  }

  function loadVpdFile(file) {
    if (!file || !mesh) return;
    const url = URL.createObjectURL(file); const loader = new MMDLoader();
    loader.loadVPD(url, false, (vpd) => {
      URL.revokeObjectURL(url); beginPoseGesture(); poseOverrides.clear(); let matched = 0;
      for (const item of vpd.bones || []) {
        const bone = mesh.skeleton.bones.find((candidate) => candidate.name === item.name); if (!bone) continue;
        poseOverrides.set(bone, clampOverride(bone, { position: new THREE.Vector3().fromArray(item.translation), quaternion: new THREE.Quaternion().fromArray(item.quaternion) })); matched += 1;
      }
      applyPoseOverrides(); syncPoseUiFromObject(); endPoseGesture(`VPD：${file.name}`); $('pose-preset-status').textContent = matched ? `VPD · ${matched} 骨骼` : 'VPD 未匹配骨骼';
    }, undefined, (error) => { URL.revokeObjectURL(url); $('pose-preset-status').textContent = 'VPD 载入失败'; console.error(error); });
  }

  function clearCameraVmd(restoreCamera = true) {
    $('camera-vmd-enabled').checked = false; $('camera-vmd-enabled').disabled = true; $('remove-camera-vmd').disabled = true;
    controls.enabled = true;
    if (cameraAnimationHelper?.camera === camera) cameraAnimationHelper.remove(camera);
    cameraAnimationHelper = null; cameraAnimationMixer = null; cameraAnimationAction = null; cameraClip = null;
    if (restoreCamera && preCameraVmdSnapshot) applyCameraSnapshot(preCameraVmdSnapshot);
    preCameraVmdSnapshot = null; $('camera-vmd-status').textContent = '未载入';
    if (!hasPlaybackSource()) animationPlaying = false;
    updateAnimationUi();
  }

  function setCameraVmdEnabled(enabled) {
    if (!cameraClip) { $('camera-vmd-enabled').checked = false; return; }
    controls.enabled = !enabled;
    if (enabled) {
      if (cameraAnimationAction) { cameraAnimationAction.paused = !animationPlaying; if (animationPlaying) cameraAnimationAction.play(); }
      if (cameraAnimationMixer) cameraAnimationMixer.setTime(playbackClock);
      cameraAnimationHelper?.update(0); $('camera-vmd-status').textContent = '已启用';
    }
    else {
      if (cameraAnimationAction) cameraAnimationAction.paused = true;
      if (preCameraVmdSnapshot) applyCameraSnapshot(preCameraVmdSnapshot);
      if (!hasPlaybackSource()) animationPlaying = false;
      $('camera-vmd-status').textContent = '已暂停';
    }
    updateAnimationUi();
  }

  function loadCameraVmdFile(file) {
    if (!file) return;
    const url = URL.createObjectURL(file); const loader = new MMDLoader(); $('camera-vmd-status').textContent = '解析中';
    loader.loadAnimation(url, camera, (clip) => {
      URL.revokeObjectURL(url);
      if (!preCameraVmdSnapshot) preCameraVmdSnapshot = cameraSnapshot();
      if (cameraAnimationHelper?.camera === camera) cameraAnimationHelper.remove(camera);
      cameraClip = clip; cameraAnimationHelper = new MMDAnimationHelper({ sync: false }); cameraAnimationHelper.add(camera, { animation: clip });
      cameraAnimationMixer = cameraAnimationHelper.objects.get(camera)?.mixer || null; cameraAnimationAction = cameraAnimationMixer?.clipAction(clip) || null;
      configureAnimationAction(cameraAnimationAction, clip); if (cameraAnimationAction) cameraAnimationAction.paused = !animationPlaying;
      if (cameraAnimationMixer) { cameraAnimationMixer.setTime(playbackClock); cameraAnimationHelper.update(0); }
      $('camera-vmd-enabled').disabled = false; $('camera-vmd-enabled').checked = true; $('remove-camera-vmd').disabled = false; controls.enabled = false;
      $('camera-vmd-status').textContent = file.name.length > 16 ? `${file.name.slice(0, 13)}…` : file.name; updateAnimationUi();
    }, undefined, (error) => { URL.revokeObjectURL(url); $('camera-vmd-status').textContent = '载入失败'; console.error(error); });
  }

  function syncAudioToTime(time, shouldPlay) {
    if (!audioElement || !Number.isFinite(audioElement.duration)) return;
    const desired = time - Number($('audio-delay').value);
    audioElement.volume = Number($('audio-volume').value); audioElement.playbackRate = Number($('animation-speed').value);
    if (desired < 0 || desired >= audioElement.duration) { audioElement.pause(); if (desired < 0) audioElement.currentTime = 0; return; }
    if (Math.abs(audioElement.currentTime - desired) > 0.18) audioElement.currentTime = desired;
    if (shouldPlay && audioElement.paused) audioElement.play().catch(() => { $('audio-status').textContent = '点击播放以授权音频'; }); else if (!shouldPlay) audioElement.pause();
  }

  function removeAudio() {
    audioElement?.pause(); if (audioObjectUrl) URL.revokeObjectURL(audioObjectUrl);
    audioElement = null; audioObjectUrl = null; $('audio-status').textContent = '未载入'; $('remove-audio').disabled = true;
    if (!hasPlaybackSource()) animationPlaying = false;
    updateAnimationUi();
  }

  function loadAudioFile(file) {
    if (!file) return; removeAudio(); audioObjectUrl = URL.createObjectURL(file); audioElement = new Audio(audioObjectUrl); audioElement.preload = 'metadata';
    audioElement.volume = Number($('audio-volume').value); $('audio-status').textContent = '读取中'; $('remove-audio').disabled = false;
    audioElement.addEventListener('loadedmetadata', () => { $('audio-status').textContent = file.name.length > 16 ? `${file.name.slice(0, 13)}…` : file.name; updateAnimationUi(); }, { once: true });
    audioElement.addEventListener('ended', () => { if (!$('animation-loop').checked && !currentClip && !cameraClip) { animationPlaying = false; updateAnimationUi(); } });
  }

  function loadClassicScript(url) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[data-lazy-src="${url}"]`);
      if (existing?.dataset.loaded === 'true') { resolve(); return; }
      const script = existing || document.createElement('script');
      script.dataset.lazySrc = url; script.src = url;
      script.onload = () => { script.dataset.loaded = 'true'; resolve(); };
      script.onerror = () => reject(new Error(`资源载入失败：${url}`));
      if (!existing) document.head.append(script);
    });
  }

  async function ensureAmmo() {
    if (physicsInitialized) return;
    if (ammoLoading) return ammoLoading;
    ammoLoading = (async () => {
      await loadClassicScript('/vendor/examples/jsm/libs/ammo.wasm.js');
      const factory = window.Ammo;
      if (typeof factory !== 'function') throw new Error('Ammo 初始化函数不可用');
      window.Ammo = await factory({ locateFile: (name) => `/vendor/examples/jsm/libs/${name}` });
      physicsInitialized = true;
    })();
    try { await ammoLoading; } finally { ammoLoading = null; }
  }

  async function setPhysicsEnabled(enabled) {
    if (!mesh) { $('physics-enabled').checked = false; return; }
    if (enabled && !physicsInitialized) {
      $('physics-enabled').disabled = true; $('physics-status').textContent = '载入引擎…';
      try { await ensureAmmo(); } catch (error) {
        console.error(error); $('physics-enabled').checked = false; $('physics-status').textContent = '载入失败'; $('physics-enabled').disabled = false; return;
      }
      $('physics-enabled').disabled = false;
      if (!$('physics-enabled').checked) return;
    }
    const time = animationMixer?.time || 0;
    physicsEnabled = enabled;
    if (enabled) {
      rebuildAnimationHelper(time);
      $('physics-status').textContent = '运行中'; $('reset-physics').disabled = false;
    } else {
      animationHelper?.enable('physics', false);
      $('physics-status').textContent = physicsInitialized ? '已停止' : '默认关闭'; $('reset-physics').disabled = true;
    }
  }

  function updatePhysicsSettings() {
    document.querySelector('[data-for="physics-gravity"]').textContent = `${Number($('physics-gravity').value).toFixed(2)}×`;
    document.querySelector('[data-for="physics-quality"]').textContent = $('physics-quality').value;
    const physics = animationHelper && mesh ? animationHelper.objects.get(mesh)?.physics : null;
    if (physics) {
      physics.maxStepNum = Number($('physics-quality').value);
      physics.setGravity(new THREE.Vector3(0, -98 * Number($('physics-gravity').value), 0));
    }
  }

  function resetPhysics() {
    const physics = animationHelper && mesh ? animationHelper.objects.get(mesh)?.physics : null;
    physics?.reset();
  }

  async function setHdrEnvironment(enabled) {
    const requestId = ++hdrRequestId;
    if (!enabled) {
      scene.environment = null; scene.background = new THREE.Color($('background').value); $('environment-status').textContent = '纯色背景'; return;
    }
    const preset = $('hdr-preset').value;
    const label = $('hdr-preset').selectedOptions[0]?.textContent || 'HDR 全景';
    $('environment-status').textContent = '载入 HDR…';
    try {
      let nextTexture = hdrTexture;
      if (!nextTexture || hdrTexturePreset !== preset) {
        nextTexture = await new RGBELoader().loadAsync(`/vendor/assets/${encodeURIComponent(preset)}.hdr`);
        nextTexture.mapping = THREE.EquirectangularReflectionMapping;
      }
      if (requestId !== hdrRequestId || !$('hdr-environment').checked || $('hdr-preset').value !== preset) {
        if (nextTexture !== hdrTexture) nextTexture.dispose();
        return;
      }
      if (nextTexture !== hdrTexture) {
        hdrTexture?.dispose();
        hdrTexture = nextTexture;
        hdrTexturePreset = preset;
      }
      scene.environment = hdrTexture; scene.background = hdrTexture;
      updatePostProcessing();
      $('environment-status').textContent = label.split(' · ')[0];
    } catch (error) {
      if (requestId !== hdrRequestId) return;
      console.error(error); $('hdr-environment').checked = false; scene.environment = null; scene.background = new THREE.Color($('background').value); $('environment-status').textContent = 'HDR 失败';
    }
  }

  function updatePostProcessing() {
    bloomPass.enabled = $('bloom-enabled').checked;
    bloomPass.strength = Number($('bloom-strength').value);
    bokehPass.enabled = $('dof-enabled').checked;
    bokehPass.uniforms.focus.value = Number($('dof-focus').value);
    bokehPass.uniforms.maxblur.value = Number($('dof-blur').value);
    bokehPass.uniforms.aperture.value = Number($('dof-blur').value) * 0.01;
    document.querySelector('[data-for="environment-intensity"]').textContent = Number($('environment-intensity').value).toFixed(2);
    document.querySelector('[data-for="hdr-rotation"]').textContent = `${Number($('hdr-rotation').value).toFixed(0)}°`;
    document.querySelector('[data-for="background-intensity"]').textContent = Number($('background-intensity').value).toFixed(2);
    document.querySelector('[data-for="background-blur"]').textContent = `${Math.round(Number($('background-blur').value) * 100)}%`;
    document.querySelector('[data-for="bloom-strength"]').textContent = Number($('bloom-strength').value).toFixed(2);
    document.querySelector('[data-for="dof-focus"]').textContent = Number($('dof-focus').value).toFixed(1);
    document.querySelector('[data-for="dof-blur"]').textContent = Number($('dof-blur').value).toFixed(4);
    if (scene.environment) {
      scene.environmentIntensity = Number($('environment-intensity').value);
      scene.backgroundIntensity = Number($('background-intensity').value);
      scene.backgroundBlurriness = Number($('background-blur').value);
      const rotation = THREE.MathUtils.degToRad(Number($('hdr-rotation').value));
      scene.backgroundRotation.set(0, rotation, 0); scene.environmentRotation.set(0, rotation, 0);
    }
  }

  function renderScene() {
    if (bloomPass.enabled || bokehPass.enabled) composer.render();
    else if (outlineEnabled && !document.hidden) effect.render(scene, camera);
    else renderer.render(scene, camera);
  }

  function captureDimensions() {
    const value = $('capture-resolution').value;
    if (value === 'viewport') return [viewport.clientWidth, viewport.clientHeight];
    return value.split('x').map(Number);
  }

  function downloadBlob(blob, name) {
    const link = document.createElement('a'); const url = URL.createObjectURL(blob);
    link.download = name; link.href = url; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function captureScreenshot() {
    const [width, height] = captureDimensions();
    const oldBackground = scene.background; const oldAspect = camera.aspect;
    const transparent = $('capture-transparent').checked;
    $('capture-status').textContent = `${width}×${height}`;
    if (transparent) scene.background = null;
    renderer.setClearAlpha(transparent ? 0 : 1);
    renderer.setSize(width, height, false); composer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix();
    renderScene();
    const blob = await new Promise((resolve) => renderer.domElement.toBlob(resolve, 'image/png'));
    if (blob) downloadBlob(blob, `miku-${width}x${height}-${Date.now()}.png`);
    scene.background = oldBackground; renderer.setClearAlpha(1); camera.aspect = oldAspect; camera.updateProjectionMatrix(); resize();
    $('capture-status').textContent = '已保存';
  }

  function startTurntableRecording() {
    if (turntableRecording || !renderer.domElement.captureStream || typeof MediaRecorder === 'undefined') {
      $('capture-status').textContent = turntableRecording ? '录制中' : '浏览器不支持'; return;
    }
    const stream = renderer.domElement.captureStream(30);
    const mimeTypes = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'];
    const mimeType = mimeTypes.find((type) => MediaRecorder.isTypeSupported(type)) || '';
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType, videoBitsPerSecond: 8000000 } : undefined);
    const chunks = [];
    recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
    recorder.onstop = () => downloadBlob(new Blob(chunks, { type: mimeType || 'video/webm' }), `miku-turntable-${Date.now()}.webm`);
    turntableRecording = {
      recorder, chunks, start: performance.now(), duration: Number($('turntable-duration').value) * 1000,
      baseRotation: root.rotation.y, autoRotate: $('auto-rotate').checked
    };
    $('auto-rotate').checked = false; $('recording-indicator').classList.add('active'); $('record-turntable').disabled = true; $('capture-status').textContent = '录制中';
    recorder.start(250);
  }

  function updateTurntableRecording(now) {
    if (!turntableRecording) return;
    const progress = Math.min(1, (now - turntableRecording.start) / turntableRecording.duration);
    root.rotation.y = turntableRecording.baseRotation + progress * Math.PI * 2;
    if (progress < 1) return;
    const finished = turntableRecording; turntableRecording = null;
    finished.recorder.stop(); root.rotation.y = finished.baseRotation; $('auto-rotate').checked = finished.autoRotate;
    $('recording-indicator').classList.remove('active'); $('record-turntable').disabled = false; $('capture-status').textContent = '已保存视频';
  }

  function disposeRenderable(object) {
    const disposedTextures = new Set();
    object?.traverse((child) => {
      child.geometry?.dispose?.();
      const materials = Array.isArray(child.material) ? child.material : child.material ? [child.material] : [];
      for (const material of materials) {
        for (const value of Object.values(material)) if (value?.isTexture && !disposedTextures.has(value)) { value.dispose(); disposedTextures.add(value); }
        material.dispose?.();
      }
    });
  }

  function clearPrimaryModel() {
    animationPlaying = false; playbackClock = 0; audioElement?.pause(); motionClips.clear(); currentClip = null; currentMotionKey = null;
    if (animationHelper?.meshes.includes(mesh)) animationHelper.remove(mesh);
    animationHelper = null; animationMixer = null; animationAction = null; physicsEnabled = false;
    $('physics-enabled').checked = false; $('reset-physics').disabled = true; $('physics-status').textContent = physicsInitialized ? '已停止' : '默认关闭';
    deactivateIkHandle(true); for (const handle of ikHandles.values()) scene.remove(handle.target); ikHandles.clear();
    transformControls.detach(); if (skeletonHelper) { scene.remove(skeletonHelper); skeletonHelper.material?.dispose?.(); skeletonHelper = null; }
    if (mesh) { root.remove(mesh); disposeRenderable(mesh); }
    mesh = null; selectedPoseObject = null; restTransforms.clear(); animationBaseTransforms.clear(); poseOverrides.clear(); poseHistory.length = 0; poseFuture.length = 0;
    root.position.set(0, 0, 0); root.quaternion.identity(); root.scale.setScalar(1); rebuildMotionSelect(); updateHistoryUi(); updateAnimationUi();
  }

  function populateModelSelect(selectedId) {
    const select = $('model-select'); select.replaceChildren();
    for (const item of modelCatalog) { const option = document.createElement('option'); option.value = item.id; option.textContent = `${item.name} · ${(item.bytes / 1048576).toFixed(1)} MB`; select.append(option); }
    if (modelCatalog.some((item) => item.id === selectedId)) select.value = selectedId;
  }

  async function loadPrimaryModel(entry) {
    if (!entry) throw new Error('没有可载入的主模型');
    $('switch-model').disabled = true; $('loading').style.display = 'block'; $('error-box').style.display = 'none'; $('progress').style.width = '2%';
    $('loading-name').textContent = entry.name; setStatus('准备载入');
    const loader = new MMDLoader();
    try {
      const loadedMesh = await new Promise((resolve, reject) => loader.load(
        entry.url,
        resolve,
        (event) => {
          const loaded = event.loaded || 0; const total = event.total || entry.bytes || 1; const percent = Math.min(99, Math.round(loaded / total * 100));
          $('progress').style.width = `${Math.max(2, percent)}%`; $('loading-percent').textContent = `${percent}%`; setStatus(`载入 ${percent}%`);
        }, reject
      ));
      if (mesh) clearPrimaryModel();
      prepareModel(loadedMesh);
      $('model-name').textContent = entry.name; $('model-meta').textContent = `PMX · ${(entry.bytes / 1048576).toFixed(1)} MB`;
      $('progress').style.width = '100%'; $('loading-percent').textContent = '100%'; setTimeout(() => { $('loading').style.display = 'none'; }, 250);
      setStatus('模型就绪', 'ready'); $('model-library-status').textContent = entry.name; $('model-select').value = entry.id;
    } finally { $('switch-model').disabled = false; }
  }

  function rebuildAccessorySelect(preferredKey = selectedAccessoryKey) {
    const select = $('accessory-select'); select.replaceChildren();
    if (!accessories.size) { const option = document.createElement('option'); option.value = ''; option.textContent = '暂无附加项'; select.append(option); selectedAccessoryKey = null; }
    else {
      for (const [key, item] of accessories) { const option = document.createElement('option'); option.value = key; option.textContent = item.name; select.append(option); }
      selectedAccessoryKey = accessories.has(preferredKey) ? preferredKey : accessories.keys().next().value; select.value = selectedAccessoryKey;
    }
    syncAccessoryUi();
  }

  function syncAccessoryUi() {
    const item = accessories.get(selectedAccessoryKey); const disabled = !item;
    ['accessory-x', 'accessory-y', 'accessory-z', 'accessory-yaw', 'accessory-scale'].forEach((id) => $(id).disabled = disabled);
    $('remove-accessory').disabled = disabled;
    if (!item) { $('accessory-status').textContent = '未选择'; return; }
    const group = item.group;
    $('accessory-x').value = String(group.position.x); $('accessory-y').value = String(group.position.y); $('accessory-z').value = String(group.position.z);
    $('accessory-yaw').value = String(THREE.MathUtils.radToDeg(group.rotation.y)); $('accessory-scale').value = String(group.scale.x);
    document.querySelector('[data-for="accessory-x"]').textContent = group.position.x.toFixed(2); document.querySelector('[data-for="accessory-y"]').textContent = group.position.y.toFixed(2);
    document.querySelector('[data-for="accessory-z"]').textContent = group.position.z.toFixed(2); document.querySelector('[data-for="accessory-yaw"]').textContent = `${THREE.MathUtils.radToDeg(group.rotation.y).toFixed(0)}°`;
    document.querySelector('[data-for="accessory-scale"]').textContent = `${group.scale.x.toFixed(2)}×`; $('accessory-status').textContent = item.name;
  }

  function updateAccessoryTransform() {
    const item = accessories.get(selectedAccessoryKey); if (!item) return;
    item.group.position.set(Number($('accessory-x').value), Number($('accessory-y').value), Number($('accessory-z').value));
    item.group.rotation.y = THREE.MathUtils.degToRad(Number($('accessory-yaw').value)); item.group.scale.setScalar(Number($('accessory-scale').value)); syncAccessoryUi();
  }

  function registerAccessory(object, name, objectUrls = []) {
    object.traverse((child) => { if (child.isMesh) { child.castShadow = true; child.receiveShadow = false; child.frustumCulled = false; } });
    const box = new THREE.Box3().setFromObject(object); const center = box.getCenter(new THREE.Vector3());
    object.position.x -= center.x; object.position.z -= center.z; object.position.y -= box.min.y;
    const group = new THREE.Group(); group.add(object); group.position.x = (accessories.size + 1) * modelHeight * 0.35; accessoryRoot.add(group);
    const key = `accessory-${++accessorySerial}`; accessories.set(key, { name, group, object, objectUrls }); selectedAccessoryKey = key; rebuildAccessorySelect(key);
    $('model-library-status').textContent = `已添加 ${name}`; return key;
  }

  async function loadServerAccessory(entry) {
    if (!entry) return; $('model-library-status').textContent = '载入附加项…';
    const object = await new Promise((resolve, reject) => new MMDLoader().load(entry.url, resolve, undefined, reject)); registerAccessory(object, entry.name);
  }

  async function loadLocalAccessory(files) {
    const list = [...files]; const modelFile = list.filter((file) => /\.(pmx|pmd)$/i.test(file.name)).sort((a, b) => a.name.localeCompare(b.name))[0];
    if (!modelFile) throw new Error('所选目录中没有 PMX/PMD 文件');
    const rootFolder = (modelFile.webkitRelativePath || modelFile.name).split('/')[0]; const urls = []; const pathMap = new Map(); const basenameMap = new Map();
    for (const file of list) {
      const fullPath = (file.webkitRelativePath || file.name).replace(/\\/g, '/'); const relative = fullPath.startsWith(`${rootFolder}/`) ? fullPath.slice(rootFolder.length + 1) : fullPath;
      const url = URL.createObjectURL(file); urls.push(url); pathMap.set(relative.toLowerCase(), url);
      const base = file.name.toLowerCase(); basenameMap.set(base, basenameMap.has(base) ? null : url);
    }
    const manager = new THREE.LoadingManager(); manager.setURLModifier((requested) => {
      let normalized; try { normalized = decodeURIComponent(requested).replace(/\\/g, '/').toLowerCase(); } catch { normalized = requested.replace(/\\/g, '/').toLowerCase(); }
      for (const [path, url] of pathMap) if (normalized.endsWith(path)) return url;
      const fallback = basenameMap.get(normalized.split('/').pop()); return fallback || requested;
    });
    const modelUrl = pathMap.get(((modelFile.webkitRelativePath || modelFile.name).replace(/\\/g, '/').split('/').slice(1).join('/') || modelFile.name).toLowerCase());
    try { const object = await new Promise((resolve, reject) => new MMDLoader(manager).load(modelUrl, resolve, undefined, reject)); registerAccessory(object, modelFile.name.replace(/\.(pmx|pmd)$/i, ''), urls); }
    catch (error) { urls.forEach(URL.revokeObjectURL); throw error; }
  }

  function removeAccessory() {
    const item = accessories.get(selectedAccessoryKey); if (!item) return;
    accessoryRoot.remove(item.group); disposeRenderable(item.object); item.objectUrls.forEach(URL.revokeObjectURL); accessories.delete(selectedAccessoryKey); rebuildAccessorySelect();
  }

  async function loadModel() {
    const configResponse = await fetch('/api/config');
    if (!configResponse.ok) throw new Error(`配置请求失败：HTTP ${configResponse.status}`);
    const config = await configResponse.json();
    modelCatalog = Array.isArray(config.models) && config.models.length ? config.models : [{ id: config.modelName, name: config.modelName, bytes: config.modelBytes, url: config.modelUrl }];
    populateModelSelect(config.selectedModel || modelCatalog[0].id); await loadPrimaryModel(modelCatalog.find((item) => item.id === (config.selectedModel || '')) || modelCatalog[0]);
  }

  document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((item) => item.classList.toggle('active', item === tab));
    document.querySelectorAll('.panel').forEach((panel) => panel.classList.toggle('active', panel.id === tab.dataset.panel));
  }));

  function releaseCameraVmdForManualControl() {
    if (!$('camera-vmd-enabled').checked) return;
    $('camera-vmd-enabled').checked = false; setCameraVmdEnabled(false);
  }
  ['focal', 'sensor'].forEach((id) => $(id).addEventListener('input', () => { releaseCameraVmdForManualControl(); updateCameraOptics(); }));
  ['cam-azimuth', 'cam-elevation', 'cam-distance', 'target-height'].forEach((id) => $(id).addEventListener('input', () => { releaseCameraVmdForManualControl(); setCameraFromUi(); }));
  ['key-intensity', 'light-azimuth', 'light-elevation', 'shadow-softness', 'key-color', 'ambient-intensity', 'fill-intensity', 'fill-color'].forEach((id) => $(id).addEventListener('input', updateLights));
  ['model-yaw', 'model-scale', 'rotate-speed', 'cast-shadow', 'show-ground', 'show-grid', 'wireframe', 'show-outline'].forEach((id) => $(id).addEventListener('input', updateModelDisplay));
  $('switch-model').addEventListener('click', () => {
    const entry = modelCatalog.find((item) => item.id === $('model-select').value); loadPrimaryModel(entry).catch(showError);
  });
  $('add-server-accessory').addEventListener('click', () => {
    const entry = modelCatalog.find((item) => item.id === $('model-select').value); loadServerAccessory(entry).catch((error) => { $('model-library-status').textContent = '附加失败'; console.error(error); });
  });
  $('add-local-accessory').addEventListener('click', () => $('local-accessory-files').click());
  $('local-accessory-files').addEventListener('change', () => {
    loadLocalAccessory($('local-accessory-files').files).catch((error) => { $('model-library-status').textContent = '本地模型失败'; console.error(error); }); $('local-accessory-files').value = '';
  });
  $('accessory-select').addEventListener('change', () => { selectedAccessoryKey = $('accessory-select').value || null; syncAccessoryUi(); });
  $('remove-accessory').addEventListener('click', removeAccessory);
  ['accessory-x', 'accessory-y', 'accessory-z', 'accessory-yaw', 'accessory-scale'].forEach((id) => $(id).addEventListener('input', updateAccessoryTransform));
  ['pose-rot-x', 'pose-rot-y', 'pose-rot-z'].forEach((id) => { $(id).addEventListener('pointerdown', beginPoseGesture); $(id).addEventListener('input', applyPoseRotation); $(id).addEventListener('change', () => endPoseGesture('旋转骨骼')); });
  ['pose-pos-x', 'pose-pos-y', 'pose-pos-z'].forEach((id) => { $(id).addEventListener('pointerdown', beginPoseGesture); $(id).addEventListener('input', applyPosePosition); $(id).addEventListener('change', () => endPoseGesture('移动骨骼')); });
  document.querySelectorAll('#pose-mode .segment').forEach((button) => button.addEventListener('click', () => setPoseMode(button.dataset.mode)));
  document.querySelectorAll('#pose-space .segment').forEach((button) => button.addEventListener('click', () => setPoseSpace(button.dataset.space)));
  $('bone-category').addEventListener('change', () => rebuildBoneSelect());
  $('bone-select').addEventListener('change', () => {
    const value = $('bone-select').value;
    if (value === 'root') selectPoseObject(root, value);
    else if (mesh?.skeleton?.bones[Number(value)]) selectPoseObject(mesh.skeleton.bones[Number(value)], value);
  });
  $('favorite-bone').addEventListener('click', () => {
    if (!selectedPoseObject || selectedPoseObject === root) return;
    if (favoriteBones.has(selectedPoseObject.name)) favoriteBones.delete(selectedPoseObject.name); else favoriteBones.add(selectedPoseObject.name);
    localStorage.setItem('mikuMmdFavorites', JSON.stringify([...favoriteBones]));
    selectPoseObject(selectedPoseObject, String(mesh.skeleton.bones.indexOf(selectedPoseObject)));
  });
  $('show-skeleton').addEventListener('input', () => { if (skeletonHelper) skeletonHelper.visible = $('show-skeleton').checked; });
  $('gizmo-size').addEventListener('input', () => {
    transformControls.setSize(Number($('gizmo-size').value));
    document.querySelector('[data-for="gizmo-size"]').textContent = `${Number($('gizmo-size').value).toFixed(2)}×`;
  });
  $('joint-limits').addEventListener('input', () => { if (selectedPoseObject) { const override = poseOverrides.get(selectedPoseObject); if (override) clampOverride(selectedPoseObject, override); applyOverrideToObject(selectedPoseObject); syncPoseUiFromObject(); } });
  $('ik-enabled').addEventListener('input', () => $('ik-enabled').checked ? activateIkHandle() : deactivateIkHandle());
  $('ik-handle').addEventListener('change', () => { if ($('ik-enabled').checked) activateIkHandle(); });
  $('ik-iterations').addEventListener('input', () => { document.querySelector('[data-for="ik-iterations"]').textContent = `${$('ik-iterations').value} 次`; if (activeIk) solveActiveIk(); });
  $('mirror-copy').addEventListener('click', () => { if (!selectedPoseObject) return; beginPoseGesture(); mirrorObjectPose(selectedPoseObject, true); endPoseGesture('镜像复制'); });
  $('swap-sides').addEventListener('click', () => { if (!mesh) return; beginPoseGesture(); swapSidePoses(); syncPoseUiFromObject(); endPoseGesture('交换左右'); });
  $('live-mirror').addEventListener('input', () => $('mirror-status').textContent = $('live-mirror').checked ? '实时对称' : '独立');
  document.querySelectorAll('.hand-preset').forEach((button) => button.addEventListener('click', () => applyHandPreset(button.dataset.hand)));
  $('apply-body-preset').addEventListener('click', () => applyBodyPreset($('body-preset').value));
  $('reset-selected-bone').addEventListener('click', resetSelectedPose);
  $('reset-all-pose').addEventListener('click', resetAllPose);
  $('undo-pose').addEventListener('click', undoPose);
  $('redo-pose').addEventListener('click', redoPose);
  $('export-pose').addEventListener('click', () => downloadBlob(new Blob([JSON.stringify(poseDocument(), null, 2)], { type: 'application/json' }), `miku-pose-${Date.now()}.json`));
  $('import-pose').addEventListener('click', () => $('pose-file').click());
  $('pose-file').addEventListener('change', async () => {
    const file = $('pose-file').files[0]; if (!file) return;
    try { const before = poseDocument(); applyPoseDocument(JSON.parse(await file.text())); poseGestureStart = before; endPoseGesture('导入姿势'); } catch (error) { alert(error.message); }
    $('pose-file').value = '';
  });
  $('load-vpd').addEventListener('click', () => $('vpd-file').click());
  $('vpd-file').addEventListener('change', () => { loadVpdFile($('vpd-file').files[0]); $('vpd-file').value = ''; });
  $('save-slot').addEventListener('click', () => {
    localStorage.setItem(`mikuMmdPoseSlot${$('pose-slot').value}`, poseSignature()); $('history-status').textContent = `槽位 ${$('pose-slot').value} 已保存`;
  });
  $('load-slot').addEventListener('click', () => {
    const saved = localStorage.getItem(`mikuMmdPoseSlot${$('pose-slot').value}`); if (!saved) { $('history-status').textContent = '槽位为空'; return; }
    try { const before = poseDocument(); applyPoseDocument(JSON.parse(saved)); poseGestureStart = before; endPoseGesture(`载入槽位 ${$('pose-slot').value}`); } catch (error) { alert(error.message); }
  });
  transformControls.addEventListener('dragging-changed', (event) => { controls.enabled = !event.value && !$('camera-vmd-enabled').checked; });
  transformControls.addEventListener('mouseDown', () => {
    gizmoInteraction = true; beginPoseGesture();
    if (animationPlaying) { animationPlaying = false; setMotionActionsPaused(true); if (cameraAnimationAction) cameraAnimationAction.paused = true; audioElement?.pause(); updateAnimationUi(); }
  });
  transformControls.addEventListener('mouseUp', () => { endPoseGesture(activeIk ? `IK ${activeIk.label}` : '操纵器调整'); setTimeout(() => { gizmoInteraction = false; }, 0); });
  transformControls.addEventListener('objectChange', () => {
    if (activeIk) solveActiveIk();
    else if (selectedPoseObject) { captureOverrideFromObject(selectedPoseObject); syncPoseUiFromObject(); }
  });
  renderer.domElement.addEventListener('pointerdown', (event) => pointerStart.set(event.clientX, event.clientY));
  renderer.domElement.addEventListener('pointerup', (event) => {
    if (event.button === 0 && pointerStart.distanceTo(new THREE.Vector2(event.clientX, event.clientY)) < 5) selectBoneFromSurface(event);
  });
  $('exposure').addEventListener('input', () => {
    renderer.toneMappingExposure = Number($('exposure').value);
    document.querySelector('[data-for="exposure"]').textContent = renderer.toneMappingExposure.toFixed(2);
  });
  $('background').addEventListener('input', () => { if (!$('hdr-environment').checked) scene.background = new THREE.Color($('background').value); });
  $('hdr-environment').addEventListener('input', () => setHdrEnvironment($('hdr-environment').checked));
  $('hdr-preset').addEventListener('change', () => { if ($('hdr-environment').checked) setHdrEnvironment(true); });
  ['environment-intensity', 'hdr-rotation', 'background-intensity', 'background-blur', 'bloom-strength', 'dof-focus', 'dof-blur'].forEach((id) => $(id).addEventListener('input', updatePostProcessing));
  ['bloom-enabled', 'dof-enabled'].forEach((id) => $(id).addEventListener('input', updatePostProcessing));
  $('reset-camera').addEventListener('click', resetCamera);
  $('apply-camera-preset').addEventListener('click', applyCameraPreset);
  $('save-camera-bookmark').addEventListener('click', () => {
    const slot = $('camera-bookmark').value; localStorage.setItem(`mikuMmdCameraBookmark${slot}`, JSON.stringify(cameraSnapshot())); $('camera-preset-status').textContent = `书签 ${slot} 已保存`;
  });
  $('load-camera-bookmark').addEventListener('click', () => {
    const slot = $('camera-bookmark').value; const saved = localStorage.getItem(`mikuMmdCameraBookmark${slot}`);
    if (!saved) { $('camera-preset-status').textContent = `书签 ${slot} 为空`; return; }
    try { releaseCameraVmdForManualControl(); applyCameraSnapshot(JSON.parse(saved)); $('camera-preset-status').textContent = `书签 ${slot}`; } catch (error) { $('camera-preset-status').textContent = '书签无效'; console.error(error); }
  });
  $('load-vmd').addEventListener('click', () => $('vmd-file').click());
  $('vmd-file').addEventListener('change', () => { loadVmdFiles($('vmd-file').files); $('vmd-file').value = ''; });
  $('motion-select').addEventListener('change', () => switchMotion($('motion-select').value, true));
  $('animation-play').addEventListener('click', toggleAnimationPlayback);
  $('animation-stop').addEventListener('click', stopAnimation);
  $('animation-remove').addEventListener('click', removeAnimation);
  $('animation-time').addEventListener('input', () => {
    animationPlaying = false; settleMotionBlend(); if (cameraAnimationAction) cameraAnimationAction.paused = true;
    setPlaybackTime(Number($('animation-time').value)); updateAnimationUi();
  });
  $('animation-speed').addEventListener('input', () => { document.querySelector('[data-for="animation-speed"]').textContent = `${Number($('animation-speed').value).toFixed(2)}×`; if (audioElement) audioElement.playbackRate = Number($('animation-speed').value); });
  $('animation-fade').addEventListener('input', () => { document.querySelector('[data-for="animation-fade"]').textContent = `${Number($('animation-fade').value).toFixed(1)} 秒`; });
  $('animation-loop').addEventListener('input', () => {
    for (const clip of motionClips.values()) configureAnimationAction(animationMixer?.clipAction(clip), clip); configureAnimationAction(cameraAnimationAction, cameraClip);
  });
  $('load-camera-vmd').addEventListener('click', () => $('camera-vmd-file').click());
  $('camera-vmd-file').addEventListener('change', () => { loadCameraVmdFile($('camera-vmd-file').files[0]); $('camera-vmd-file').value = ''; });
  $('remove-camera-vmd').addEventListener('click', () => clearCameraVmd(true));
  $('camera-vmd-enabled').addEventListener('input', () => setCameraVmdEnabled($('camera-vmd-enabled').checked));
  $('load-audio').addEventListener('click', () => $('audio-file').click());
  $('audio-file').addEventListener('change', () => { loadAudioFile($('audio-file').files[0]); $('audio-file').value = ''; });
  $('remove-audio').addEventListener('click', removeAudio);
  $('audio-delay').addEventListener('input', () => { document.querySelector('[data-for="audio-delay"]').textContent = `${Number($('audio-delay').value).toFixed(2)} 秒`; syncAudioToTime(playbackTime(), animationPlaying); });
  $('audio-volume').addEventListener('input', () => { document.querySelector('[data-for="audio-volume"]').textContent = `${Math.round(Number($('audio-volume').value) * 100)}%`; if (audioElement) audioElement.volume = Number($('audio-volume').value); });
  $('physics-enabled').addEventListener('input', () => setPhysicsEnabled($('physics-enabled').checked));
  ['physics-gravity', 'physics-quality'].forEach((id) => $(id).addEventListener('input', updatePhysicsSettings));
  $('reset-physics').addEventListener('click', resetPhysics);
  $('morph-search').addEventListener('input', () => {
    const query = $('morph-search').value.trim().toLowerCase();
    document.querySelectorAll('.morph-control').forEach((row) => row.style.display = row.dataset.name.includes(query) ? '' : 'none');
  });
  $('clear-morphs').addEventListener('click', () => {
    beginPoseGesture();
    if (mesh?.morphTargetInfluences) mesh.morphTargetInfluences.fill(0);
    syncMorphControls(); $('expression-status').textContent = '自然'; endPoseGesture('表情归零');
  });
  document.querySelectorAll('.expression-preset').forEach((button) => button.addEventListener('click', () => applyExpressionPreset(button.dataset.expression)));
  $('eye-strength').addEventListener('input', () => document.querySelector('[data-for="eye-strength"]').textContent = `${Math.round(Number($('eye-strength').value) * 100)}%`);
  $('eye-tracking').addEventListener('input', () => { if (!$('eye-tracking').checked) eyeBones.forEach(applyOverrideToObject); });
  $('auto-blink').addEventListener('input', () => { if (!$('auto-blink').checked) { setMorphsByPatterns(/まばたき|両目閉|blink/i, 0); syncMorphControls(); } });
  $('screenshot').addEventListener('click', captureScreenshot);
  $('turntable-duration').addEventListener('input', () => document.querySelector('[data-for="turntable-duration"]').textContent = `${$('turntable-duration').value} 秒`);
  $('record-turntable').addEventListener('click', startTurntableRecording);
  $('fullscreen').addEventListener('click', async () => {
    if (!document.fullscreenElement) await $('app').requestFullscreen(); else await document.exitFullscreen();
  });
  $('mobile-open').addEventListener('click', () => inspector.classList.add('open'));
  $('panel-toggle').addEventListener('click', () => inspector.classList.remove('open'));
  window.addEventListener('keydown', (event) => {
    if (event.target.matches('input, select, textarea')) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? redoPose() : undoPose(); }
    else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') { event.preventDefault(); redoPose(); }
    else if (event.key.toLowerCase() === 'r') setPoseMode('rotate');
    else if (event.key.toLowerCase() === 't') setPoseMode('translate');
  });

  scene.background = new THREE.Color($('background').value);
  updateCameraOptics();
  updateLights();
  updateModelDisplay();
  updatePostProcessing();
  updatePhysicsSettings();
  window.lucide?.createIcons({ attrs: { 'stroke-width': 1.8 } });

  const clock = new THREE.Clock();
  let fpsFrames = 0;
  let fpsElapsed = 0;
  function updateAutomaticBlink(delta) {
    if (!$('auto-blink').checked || !mesh?.morphTargetDictionary) return;
    blinkClock = (blinkClock + delta) % 4.2;
    const value = blinkClock < 0.16 ? Math.sin(blinkClock / 0.16 * Math.PI) : 0;
    setMorphsByPatterns(/まばたき|両目閉|blink/i, value);
  }

  function updateSelectionVisual() {
    const object = activeIk?.target || (selectedPoseObject !== root ? selectedPoseObject : null);
    if (!object || !mesh) { boneMarker.visible = false; $('bone-label').style.display = 'none'; return; }
    const world = object.getWorldPosition(new THREE.Vector3());
    boneMarker.visible = !activeIk; boneMarker.position.copy(world);
    const projected = world.clone().project(camera);
    if (projected.z < -1 || projected.z > 1) { $('bone-label').style.display = 'none'; return; }
    const x = (projected.x * 0.5 + 0.5) * viewport.clientWidth;
    const y = (-projected.y * 0.5 + 0.5) * viewport.clientHeight;
    $('bone-label').style.display = 'block'; $('bone-label').style.left = `${x}px`; $('bone-label').style.top = `${y}px`;
    $('bone-label').textContent = activeIk ? `IK ${activeIk.label}` : boneAlias(selectedPoseObject.name);
  }

  function animate(now) {
    requestAnimationFrame(animate);
    const delta = Math.min(clock.getDelta(), 0.1);
    const playbackSpeed = Number($('animation-speed').value);
    let playbackWrapped = false;
    if (animationPlaying) {
      playbackClock += delta * playbackSpeed;
      const duration = playbackDuration();
      if ($('animation-loop').checked && duration > 0 && playbackClock >= duration) { playbackClock %= duration; playbackWrapped = true; }
    }
    if (animationHelper) {
      if (animationMixer) animationMixer.timeScale = playbackSpeed;
      if (playbackWrapped && animationMixer) { settleMotionBlend(); animationMixer.setTime(playbackClock); animationHelper.update(0); }
      else animationHelper.update(delta);
      if (animationMixer) for (const [key, clip] of motionClips) {
        const action = animationMixer.clipAction(clip);
        if (key !== currentMotionKey && action.getEffectiveWeight() <= 0.0001) action.paused = true;
      }
      if (currentClip) for (const object of restTransforms.keys()) animationBaseTransforms.set(object, cloneTransform(object));
      applyPoseOverrides();
    } else applyPoseOverrides();
    if (cameraAnimationHelper && $('camera-vmd-enabled').checked) {
      if (cameraAnimationMixer) cameraAnimationMixer.timeScale = playbackSpeed;
      if (playbackWrapped && cameraAnimationMixer) { cameraAnimationMixer.setTime(playbackClock); cameraAnimationHelper.update(0); }
      else cameraAnimationHelper.update(delta);
    }
    if (activeIk) solveActiveIk();
    updateEyeTracking(); updateAutomaticBlink(delta);
    if ($('auto-rotate').checked && mesh) root.rotation.y += delta * 0.35 * Number($('rotate-speed').value);
    if (audioElement && animationPlaying) syncAudioToTime(playbackTime(), true);
    if (animationPlaying && !$('animation-loop').checked && playbackTime() >= playbackDuration() - 0.001) {
      animationPlaying = false; setMotionActionsPaused(true); if (cameraAnimationAction) cameraAnimationAction.paused = true; audioElement?.pause();
    }
    updateTurntableRecording(now); if (!cameraAnimationHelper || !$('camera-vmd-enabled').checked) controls.update(); updateSelectionVisual(); updateAnimationUi(); renderScene();
    fpsFrames += 1; fpsElapsed += delta;
    if (fpsElapsed >= 0.75) {
      $('fps').textContent = `${Math.round(fpsFrames / fpsElapsed)} FPS`;
      fpsFrames = 0; fpsElapsed = 0;
    }
  }
  animate(performance.now());
  loadModel().catch(showError);
</script>
</body>
</html>
'''


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], model_dir: Path, model_file: Path):
        super().__init__(address, ViewerHandler)
        self.model_dir = model_dir
        self.model_file = model_file


class ViewerHandler(BaseHTTPRequestHandler):
    server_version = "MikuMMDViewer/1.0"

    @property
    def viewer_server(self) -> ViewerServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format_string: str, *args: object) -> None:
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), format_string % args))
        sys.stdout.flush()

    def do_HEAD(self) -> None:
        self._dispatch(send_body=False)

    def do_GET(self) -> None:
        self._dispatch(send_body=True)

    def _dispatch(self, send_body: bool) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        if path in {"/", "/index.html"}:
            self._send_bytes(HTML.encode("utf-8"), "text/html; charset=utf-8", send_body, "no-cache")
        elif path == "/api/config":
            model = self.viewer_server.model_file
            model_files = sorted(self.viewer_server.model_dir.glob("*.pmx")) + sorted(self.viewer_server.model_dir.glob("*.pmd"))
            payload = json.dumps({
                "modelName": model.stem,
                "modelBytes": model.stat().st_size,
                "modelUrl": "/model/" + urllib.parse.quote(model.name),
                "selectedModel": model.name,
                "models": [
                    {
                        "id": item.name,
                        "name": item.stem,
                        "bytes": item.stat().st_size,
                        "url": "/model/" + urllib.parse.quote(item.name),
                    }
                    for item in model_files
                ],
            }, ensure_ascii=False).encode("utf-8")
            self._send_bytes(payload, "application/json; charset=utf-8", send_body, "no-cache")
        elif path == "/healthz":
            self._send_bytes(b"ok\n", "text/plain; charset=utf-8", send_body, "no-cache")
        elif path.startswith("/model/"):
            self._serve_model_asset(path.removeprefix("/model/"), send_body)
        elif path.startswith("/vendor/"):
            self._serve_vendor(path.removeprefix("/vendor/"), send_body)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _serve_model_asset(self, relative_url: str, send_body: bool) -> None:
        relative = Path(posixpath.normpath(relative_url.replace("\\", "/")))
        if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() not in ALLOWED_ASSET_SUFFIXES:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        root = self.viewer_server.model_dir.resolve()
        candidate = (root / relative).resolve()
        if candidate.is_relative_to(root) and not candidate.is_file():
            candidate = self._resolve_case_insensitive(root, relative)
        if candidate is None or not candidate.is_relative_to(root) or not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        stat = candidate.stat()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if send_body:
            with candidate.open("rb") as source:
                while chunk := source.read(1024 * 256):
                    self.wfile.write(chunk)

    @staticmethod
    def _resolve_case_insensitive(root: Path, relative: Path) -> Path | None:
        current = root
        try:
            for part in relative.parts:
                exact = current / part
                if exact.exists():
                    current = exact
                    continue
                matches = [entry for entry in current.iterdir() if entry.name.casefold() == part.casefold()]
                if len(matches) != 1:
                    return None
                current = matches[0]
            resolved = current.resolve()
            return resolved if resolved.is_relative_to(root) else None
        except OSError:
            return None

    def _serve_vendor(self, relative_url: str, send_body: bool) -> None:
        normalized = posixpath.normpath(relative_url)
        if ".." in normalized.split("/") or not normalized.endswith((".js", ".wasm", ".hdr")):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if normalized.startswith("assets/") and (hdr_file := HDR_ASSETS.get(normalized.removeprefix("assets/"))):
            url = f"https://cdn.jsdelivr.net/gh/mrdoob/three.js@r170/examples/textures/equirectangular/{hdr_file}"
        elif normalized == "lucide/lucide.min.js":
            url = "https://cdn.jsdelivr.net/npm/lucide@0.468.0/dist/umd/lucide.min.js"
        elif normalized.startswith("build/"):
            upstream_path = normalized
            url = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/{upstream_path}"
        elif normalized.startswith("examples/jsm/"):
            upstream_path = normalized
            url = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/{upstream_path}"
        else:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            with VENDOR_CACHE_LOCK:
                content = VENDOR_CACHE.get(url)
            if content is None:
                request = urllib.request.Request(url, headers={"User-Agent": self.server_version})
                with urllib.request.urlopen(request, timeout=30) as response:
                    content = response.read()
                with VENDOR_CACHE_LOCK:
                    VENDOR_CACHE[url] = content
            if normalized.endswith(".js"):
                content_type = "text/javascript; charset=utf-8"
            elif normalized.endswith(".wasm"):
                content_type = "application/wasm"
            else:
                content_type = "image/vnd.radiance"
            self._send_bytes(content, content_type, send_body, "public, max-age=86400")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            self.send_error(HTTPStatus.BAD_GATEWAY, f"Three.js upstream unavailable: {error}")

    def _send_bytes(self, data: bytes, content_type: str, send_body: bool, cache_control: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if send_body:
            self.wfile.write(data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve a local MMD PMX model in a browser.")
    parser.add_argument("--host", default="0.0.0.0", help="listen address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"listen port (default: {DEFAULT_PORT})")
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=DEFAULT_MODEL_DIR,
        help=f"directory containing PMX and textures (default: {DEFAULT_MODEL_DIR})",
    )
    parser.add_argument("--model", type=Path, help="PMX filename inside --model-dir")
    return parser.parse_args()


def resolve_model(args: argparse.Namespace) -> tuple[Path, Path]:
    model_dir = args.model_dir.expanduser().resolve()
    if not model_dir.is_dir():
        raise FileNotFoundError(f"model directory does not exist: {model_dir}")
    if args.model:
        model_file = (model_dir / args.model).resolve()
    else:
        candidates = sorted(model_dir.glob("*.pmx")) + sorted(model_dir.glob("*.pmd"))
        if not candidates:
            raise FileNotFoundError(f"no PMX/PMD model found in: {model_dir}")
        model_file = candidates[0].resolve()
    if not model_file.is_relative_to(model_dir) or not model_file.is_file():
        raise FileNotFoundError(f"model file does not exist inside model directory: {model_file}")
    return model_dir, model_file


def get_lan_ip() -> str | None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 80))
        address = probe.getsockname()[0]
        return None if address.startswith("127.") else address
    except OSError:
        return None
    finally:
        probe.close()


def main() -> int:
    args = parse_args()
    try:
        model_dir, model_file = resolve_model(args)
    except (FileNotFoundError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    server = ViewerServer((args.host, args.port), model_dir, model_file)
    shutdown_once = threading.Event()

    def request_shutdown(signum: int, _frame: object) -> None:
        if shutdown_once.is_set():
            return
        shutdown_once.set()
        print(f"\nreceived signal {signum}, stopping viewer...")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    print(f"MMD model : {model_file}")
    print(f"Local URL : http://127.0.0.1:{args.port}/")
    if lan_ip := get_lan_ip():
        print(f"LAN URL   : http://{lan_ip}:{args.port}/")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
