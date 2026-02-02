import * as Cesium from 'cesium';
import './style.css';
import { initApp } from './app';

(window as Window & { CESIUM_BASE_URL?: string }).CESIUM_BASE_URL = `${import.meta.env.BASE_URL}cesium/`;

Cesium.Ion.defaultAccessToken = import.meta.env.VITE_CESIUM_ION_TOKEN ?? '';

initApp();
