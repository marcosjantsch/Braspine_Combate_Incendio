const DEFAULT_RANGE_KM = 25;
const EARTH_RADIUS_METERS = 6371008.8;
const WEB_MERCATOR = 'EPSG:3857';
const WGS84 = 'EPSG:4326';

const map = L.map('map', {
  boxZoom: false,
  zoomControl: false,
}).setView([-15.8, -47.9], 5);

L.control.zoom({ position: 'bottomright' }).addTo(map);

L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  {
    maxZoom: 19,
    attribution:
      'Tiles &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community',
  }
).addTo(map);

const sightLayer = L.layerGroup().addTo(map);
const intersectionLayer = L.layerGroup().addTo(map);
const previewLayer = L.layerGroup().addTo(map);

const towerRows = document.querySelector('#towerRows');
const towerTemplate = document.querySelector('#towerTemplate');
const towerForm = document.querySelector('#towerForm');
const addTowerButton = document.querySelector('#addTower');
const rangeKmInput = document.querySelector('#rangeKm');
const rangeSummary = document.querySelector('#rangeSummary');
const intersectionCount = document.querySelector('#intersectionCount');
const loadStatus = document.querySelector('#loadStatus');
const loadDot = document.querySelector('#loadDot');

let lastMapLatLng = null;
let isPointerOverMap = false;
let pendingCoordinateMarker = null;
let selectedTowerIndex = null;

function setStatus(message, state = 'loading') {
  loadStatus.textContent = message;
  loadDot.className = `status-dot ${state}`;
}

function addTowerRow(values = {}) {
  const fragment = towerTemplate.content.cloneNode(true);
  const row = fragment.querySelector('.tower-row');
  row.querySelector('[name="x"]').value = values.x ?? '';
  row.querySelector('[name="y"]').value = values.y ?? '';
  row.querySelector('[name="angle"]').value = values.angle ?? '0';
  row.querySelector('.remove-tower').addEventListener('click', () => {
    if (towerRows.querySelectorAll('.tower-row').length === 1) {
      selectedTowerIndex = null;
      map.dragging.enable();
      row.querySelector('[name="x"]').value = '';
      row.querySelector('[name="y"]').value = '';
      row.querySelector('[name="angle"]').value = '0';
      sightLayer.clearLayers();
      intersectionLayer.clearLayers();
      intersectionCount.textContent = '0';
      clearPendingCoordinate();
      row.querySelector('[name="x"]').focus();
      setStatus('Dados do ponto limpos.', 'ready');
      return;
    }

    selectedTowerIndex = null;
    map.dragging.enable();
    row.remove();
    refreshRowTitles();
    renderSightLines({ validate: false });
  });
  towerRows.append(row);
  refreshRowTitles();
}

function refreshRowTitles() {
  const rows = [...towerRows.querySelectorAll('.tower-row')];
  rows.forEach((row, index) => {
    row.querySelector('.tower-name').textContent = `Ponto ${index + 1}`;
    row.classList.toggle('is-selected', index === selectedTowerIndex);
  });
}

function readTowers() {
  return [...towerRows.querySelectorAll('.tower-row')].map((row, index) => {
    const x = Number(row.querySelector('[name="x"]').value);
    const y = Number(row.querySelector('[name="y"]').value);
    const angle = Number(row.querySelector('[name="angle"]').value);
    return { index, x, y, angle };
  });
}

function getRangeKm() {
  const value = Number(rangeKmInput.value);
  return Number.isFinite(value) && value > 0 ? value : DEFAULT_RANGE_KM;
}

function getRangeMeters() {
  return getRangeKm() * 1000;
}

function updateRangeSummary() {
  rangeSummary.textContent = `${getRangeKm().toLocaleString('pt-BR', {
    maximumFractionDigits: 2,
  })} km`;
}

function degreesToRadians(value) {
  return (value * Math.PI) / 180;
}

function radiansToDegrees(value) {
  return (value * 180) / Math.PI;
}

function normalizeLongitude(value) {
  return ((value + 540) % 360) - 180;
}

function normalizeAngle(value) {
  return ((value % 360) + 360) % 360;
}

function lngLatToMapPoint(lng, lat) {
  const [x, y] = proj4(WGS84, WEB_MERCATOR, [lng, lat]);
  return { x, y };
}

function mapPointToLatLng(point) {
  const [lng, lat] = proj4(WEB_MERCATOR, WGS84, [point.x, point.y]);
  return L.latLng(lat, lng);
}

function getBearingDegrees(startLatLng, endLatLng) {
  const startLat = degreesToRadians(startLatLng.lat);
  const endLat = degreesToRadians(endLatLng.lat);
  const deltaLng = degreesToRadians(endLatLng.lng - startLatLng.lng);
  const y = Math.sin(deltaLng) * Math.cos(endLat);
  const x =
    Math.cos(startLat) * Math.sin(endLat) -
    Math.sin(startLat) * Math.cos(endLat) * Math.cos(deltaLng);
  return normalizeAngle(radiansToDegrees(Math.atan2(y, x)));
}

function getPointToSegmentDistance(point, start, end) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;

  if (dx === 0 && dy === 0) {
    return point.distanceTo(start);
  }

  const ratio = Math.max(
    0,
    Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy))
  );
  const projection = L.point(start.x + ratio * dx, start.y + ratio * dy);
  return point.distanceTo(projection);
}

function findNearestSightLine(latLng) {
  const towers = readTowers();
  let nearest = null;
  let nearestDistance = Infinity;
  const clickPoint = map.latLngToContainerPoint(latLng);

  towers.forEach((tower) => {
    const segment = makeSightSegment(tower);
    const start = map.latLngToContainerPoint(segment.startLatLng);
    const end = map.latLngToContainerPoint(segment.endLatLng);
    const distance = getPointToSegmentDistance(clickPoint, start, end);

    if (distance < nearestDistance) {
      nearestDistance = distance;
      nearest = tower.index;
    }
  });

  return nearestDistance <= 18 ? nearest : null;
}

function selectNearestSightLine(latLng) {
  if (!towerForm.checkValidity()) {
    return;
  }

  const towerIndex = findNearestSightLine(latLng);
  if (towerIndex === null) {
    clearSelectedSightLine();
    setStatus('Use Shift+clique sobre ou perto de uma linha para rotacionar.', 'error');
    return;
  }

  selectSightLine(towerIndex);
}

function findNextEmptyTowerRow() {
  return [...towerRows.querySelectorAll('.tower-row')].find((row) => {
    const xInput = row.querySelector('[name="x"]');
    const yInput = row.querySelector('[name="y"]');
    return !xInput.value || !yInput.value;
  });
}

function clearPendingCoordinate() {
  previewLayer.clearLayers();
  pendingCoordinateMarker = null;
  map.closePopup();
}

function makeCoordinatePopup(latLng) {
  const popup = document.createElement('div');
  popup.className = 'coordinate-popup';

  const title = document.createElement('strong');
  title.textContent = 'Usar esta coordenada?';

  const coordinate = document.createElement('p');
  coordinate.textContent = `${latLng.lng.toFixed(6)}, ${latLng.lat.toFixed(6)}`;

  const actions = document.createElement('div');
  actions.className = 'coordinate-popup-actions';

  const acceptButton = document.createElement('button');
  acceptButton.type = 'button';
  acceptButton.className = 'popup-accept';
  acceptButton.textContent = 'Aceitar';
  acceptButton.addEventListener('click', () => fillNextTowerFromMap(latLng));

  const cancelButton = document.createElement('button');
  cancelButton.type = 'button';
  cancelButton.className = 'popup-cancel';
  cancelButton.textContent = 'Cancelar';
  cancelButton.addEventListener('click', clearPendingCoordinate);

  actions.append(acceptButton, cancelButton);
  popup.append(title, coordinate, actions);
  return popup;
}

function previewCoordinate(latLng) {
  if (!latLng) {
    setStatus('Passe o mouse sobre o mapa antes de capturar a coordenada.', 'error');
    return;
  }

  clearPendingCoordinate();
  pendingCoordinateMarker = L.circleMarker(latLng, {
    radius: 8,
    color: '#ffffff',
    weight: 3,
    fillColor: '#c98216',
    fillOpacity: 1,
  }).addTo(previewLayer);

  pendingCoordinateMarker
    .bindPopup(makeCoordinatePopup(latLng), {
      closeButton: false,
      autoClose: false,
      closeOnClick: false,
      className: 'coordinate-leaflet-popup',
    })
    .openPopup();

  setStatus('Confirme a coordenada no pop-up do mapa.', 'ready');
}

function fillNextTowerFromMap(latLng) {
  if (!latLng) {
    setStatus('Passe o mouse sobre o mapa antes de capturar a coordenada.', 'error');
    return;
  }

  let row = findNextEmptyTowerRow();
  if (!row) {
    addTowerRow();
    row = towerRows.querySelector('.tower-row:last-child');
  }

  row.querySelector('[name="x"]').value = latLng.lng.toFixed(6);
  row.querySelector('[name="y"]').value = latLng.lat.toFixed(6);
  row.querySelector('[name="angle"]').value = '0';
  clearPendingCoordinate();
  setStatus('Coordenada aceita com angulo inicial de 0 graus.', 'ready');
  renderSightLines({ validate: false });
}

function clearSelectedSightLine() {
  if (selectedTowerIndex === null) {
    return;
  }

  selectedTowerIndex = null;
  map.dragging.enable();
  map.getContainer().classList.remove('is-rotating-line');
  refreshRowTitles();
  renderSightLines({ validate: false, fit: false });
  setStatus('Rotacao da linha encerrada.', 'ready');
}

function selectSightLine(towerIndex) {
  selectedTowerIndex = towerIndex;
  map.dragging.disable();
  map.getContainer().classList.add('is-rotating-line');
  refreshRowTitles();
  renderSightLines({ validate: false, fit: false });
  setStatus('Linha selecionada. Mova o mouse no mapa para rotacionar.', 'ready');
}

function rotateSelectedSightLine(latLng) {
  if (selectedTowerIndex === null || pendingCoordinateMarker) {
    return;
  }

  const row = [...towerRows.querySelectorAll('.tower-row')][selectedTowerIndex];
  if (!row) {
    selectedTowerIndex = null;
    map.dragging.enable();
    map.getContainer().classList.remove('is-rotating-line');
    return;
  }

  const lng = Number(row.querySelector('[name="x"]').value);
  const lat = Number(row.querySelector('[name="y"]').value);
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
    return;
  }

  const angle = getBearingDegrees(L.latLng(lat, lng), latLng);
  row.querySelector('[name="angle"]').value = angle.toFixed(2);
  renderSightLines({ validate: false, fit: false });
}

function getEndpoint(tower) {
  const bearing = degreesToRadians(tower.angle);
  const distance = getRangeMeters() / EARTH_RADIUS_METERS;
  const startLat = degreesToRadians(tower.y);
  const startLng = degreesToRadians(tower.x);
  const endLat = Math.asin(
    Math.sin(startLat) * Math.cos(distance) +
      Math.cos(startLat) * Math.sin(distance) * Math.cos(bearing)
  );
  const endLng =
    startLng +
    Math.atan2(
      Math.sin(bearing) * Math.sin(distance) * Math.cos(startLat),
      Math.cos(distance) - Math.sin(startLat) * Math.sin(endLat)
    );

  return {
    x: normalizeLongitude(radiansToDegrees(endLng)),
    y: radiansToDegrees(endLat),
  };
}

function makeSightSegment(tower) {
  const end = getEndpoint(tower);
  const startLatLng = L.latLng(tower.y, tower.x);
  const endLatLng = L.latLng(end.y, end.x);
  return {
    tower,
    start: lngLatToMapPoint(tower.x, tower.y),
    end: lngLatToMapPoint(end.x, end.y),
    startLatLng,
    endLatLng,
  };
}

function findSegmentIntersection(a, b) {
  const x1 = a.start.x;
  const y1 = a.start.y;
  const x2 = a.end.x;
  const y2 = a.end.y;
  const x3 = b.start.x;
  const y3 = b.start.y;
  const x4 = b.end.x;
  const y4 = b.end.y;
  const denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4);

  if (Math.abs(denominator) < 0.000001) {
    return null;
  }

  const t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denominator;
  const u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denominator;

  if (t < 0 || t > 1 || u < 0 || u > 1) {
    return null;
  }

  return {
    x: x1 + t * (x2 - x1),
    y: y1 + t * (y2 - y1),
  };
}

function renderSightLines({ validate = true, fit = true } = {}) {
  updateRangeSummary();

  if (validate && !towerForm.reportValidity()) {
    return;
  }

  if (!validate && !towerForm.checkValidity()) {
    return;
  }

  sightLayer.clearLayers();
  intersectionLayer.clearLayers();

  const segments = readTowers().map(makeSightSegment);
  const bounds = L.latLngBounds([]);

  segments.forEach((segment) => {
    const isSelected = segment.tower.index === selectedTowerIndex;
    const line = L.polyline([segment.startLatLng, segment.endLatLng], {
      color: isSelected ? '#ffcf4a' : '#c98216',
      weight: isSelected ? 6 : 3,
      opacity: 0.95,
    }).bindTooltip(
      `Ponto ${segment.tower.index + 1}: ${segment.tower.angle.toFixed(2)} graus / ${getRangeKm().toLocaleString('pt-BR', { maximumFractionDigits: 2 })} km`
    );

    const hitLine = L.polyline([segment.startLatLng, segment.endLatLng], {
      color: '#ffffff',
      weight: 18,
      opacity: 0.001,
    });

    const handleLineSelection = (event) => {
      if (event.originalEvent.shiftKey) {
        L.DomEvent.stop(event.originalEvent);
        selectSightLine(segment.tower.index);
        return;
      }

      clearSelectedSightLine();
    };

    line.on('click', handleLineSelection);
    hitLine.on('click', handleLineSelection);

    const marker = L.circleMarker(segment.startLatLng, {
      radius: isSelected ? 8 : 6,
      color: '#ffffff',
      weight: 2,
      fillColor: isSelected ? '#ffcf4a' : '#0b7189',
      fillOpacity: 1,
    }).bindTooltip(`Ponto ${segment.tower.index + 1}`, { permanent: false });

    sightLayer.addLayer(hitLine);
    sightLayer.addLayer(line);
    sightLayer.addLayer(marker);
    bounds.extend(segment.startLatLng);
    bounds.extend(segment.endLatLng);
  });

  const intersections = [];
  for (let i = 0; i < segments.length; i += 1) {
    for (let j = i + 1; j < segments.length; j += 1) {
      const point = findSegmentIntersection(segments[i], segments[j]);
      if (point) {
        intersections.push(point);
      }
    }
  }

  intersections.forEach((point, index) => {
    const latLng = mapPointToLatLng(point);
    L.circleMarker(latLng, {
      radius: 7,
      color: '#ffffff',
      weight: 2,
      fillColor: '#bd3d34',
      fillOpacity: 1,
    })
      .bindTooltip(`Cruzamento ${index + 1}`)
      .addTo(intersectionLayer);
    bounds.extend(latLng);
  });

  intersectionCount.textContent = String(intersections.length);

  if (fit && bounds.isValid()) {
    map.fitBounds(bounds.pad(0.35), { maxZoom: 14 });
  }
}

addTowerButton.addEventListener('click', () => addTowerRow());
rangeKmInput.addEventListener('input', () => {
  updateRangeSummary();
  renderSightLines({ validate: false, fit: false });
});
map.on('mousemove', (event) => {
  lastMapLatLng = event.latlng;
  rotateSelectedSightLine(event.latlng);
});
map.on('mouseover', () => {
  isPointerOverMap = true;
});
map.on('mouseout', () => {
  isPointerOverMap = false;
});
map.on('click', (event) => {
  if (event.originalEvent.ctrlKey) {
    event.originalEvent.preventDefault();
    previewCoordinate(event.latlng);
    return;
  }

  if (event.originalEvent.shiftKey) {
    event.originalEvent.preventDefault();
    selectNearestSightLine(event.latlng);
    return;
  }

  clearSelectedSightLine();
});
map.on('mousedown', (event) => {
  if (event.originalEvent.shiftKey || selectedTowerIndex !== null) {
    event.originalEvent.preventDefault();
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    clearSelectedSightLine();
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Control' && !event.repeat && isPointerOverMap) {
    event.preventDefault();
    previewCoordinate(lastMapLatLng);
  }
});
towerForm.addEventListener('submit', (event) => {
  event.preventDefault();
  renderSightLines();
});

addTowerRow();
updateRangeSummary();
setStatus('Mapa base carregado.', 'ready');
map.invalidateSize();
