import * as Cesium from 'cesium';
import 'cesium/Build/Cesium/Widgets/widgets.css';
import './aligner.css';
import { initAligner } from './aligner';

(window as Window & { CESIUM_BASE_URL?: string }).CESIUM_BASE_URL =
  `${import.meta.env.BASE_URL}cesium/`;
Cesium.Ion.defaultAccessToken = import.meta.env.VITE_CESIUM_ION_TOKEN ?? '';

initAligner().catch((error) => {
  const status = document.getElementById('status');
  if (status) status.textContent = `failed: ${String(error)}`;
  console.error(error);
});
