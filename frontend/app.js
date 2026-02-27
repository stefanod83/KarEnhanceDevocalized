import WaveSurfer from 'https://unpkg.com/wavesurfer.js@7/dist/wavesurfer.esm.js';

// --- State ---
let sessionId = null;
let analysisData = null;
let wsVocal = null;
let wsInstrumental = null;

// --- DOM refs ---
const vocalInput = document.getElementById('vocal-file');
const instInput = document.getElementById('instrumental-file');
const analyzeBtn = document.getElementById('analyze-btn');
const analyzeStatus = document.getElementById('analyze-status');

const sensitivitySlider = document.getElementById('sensitivity');
const sensitivityRow = document.getElementById('sensitivity-row');
const bandCountSlider = document.getElementById('band-count');

const eqSlider = document.getElementById('eq-level');
const eqLabel = document.getElementById('eq-label');
const eqValSpan = document.getElementById('eq-val');
const eqHelp = document.getElementById('eq-help');
const stereoCheck = document.getElementById('stereo-widen');
const normSelect = document.getElementById('normalization');

const processBtn = document.getElementById('process-btn');
const progressContainer = document.getElementById('progress-container');
const progressFill = document.getElementById('progress-fill');
const progressText = document.getElementById('progress-text');

const downloadBtn = document.getElementById('download-btn');

// --- Mode handling ---
const modeRadios = document.querySelectorAll('input[name="analysis-mode"]');
const referenceLabel = document.getElementById('reference-label');
const referenceHint = document.getElementById('reference-hint');
const waveformRefLabel = document.getElementById('waveform-ref-label');
const heatmapLabel = document.getElementById('heatmap-label');

function getMode() {
    const checked = document.querySelector('input[name="analysis-mode"]:checked');
    return checked ? checked.value : 'mix';
}

function updateModeUI() {
    const mode = getMode();
    if (mode === 'mix') {
        referenceLabel.textContent = 'Mix Originale';
        referenceHint.textContent = 'Il mix originale completo (voce + strumenti)';
        sensitivityRow.classList.add('hidden');
        waveformRefLabel.textContent = 'Mix Originale';
        heatmapLabel.innerHTML = 'Delta energia mix vs strumentale <span class="waveform-hint">(pi\u00f9 caldo = pi\u00f9 compensazione)</span>';
        // EQ slider becomes Compensazione 0-100%
        updateEqForMixMode();
    } else {
        referenceLabel.textContent = 'Traccia Vocale';
        referenceHint.textContent = 'La traccia con la voce isolata (da UVR/Demucs)';
        sensitivityRow.classList.remove('hidden');
        waveformRefLabel.textContent = 'Vocale';
        heatmapLabel.innerHTML = 'Mappa intensit\u00e0 per banda <span class="waveform-hint">(pi\u00f9 caldo = pi\u00f9 intervento)</span>';
        // EQ slider back to EQ Level 0-10
        updateEqForVocalMode();
    }
}

function updateEqForMixMode() {
    const val = parseInt(eqSlider.value);
    const pct = val * 10;
    eqLabel.innerHTML = `Compensazione <span id="eq-val">${pct}%</span>`;
    const helpEl = document.getElementById('eq-help');
    if (pct === 0) helpEl.textContent = '0%: la strumentale non viene modificata';
    else if (pct === 100) helpEl.textContent = '100%: la strumentale raggiunge i livelli del mix originale';
    else helpEl.textContent = `${pct}%: compensazione parziale`;
}

function updateEqForVocalMode() {
    const val = parseInt(eqSlider.value);
    eqLabel.innerHTML = `EQ Level <span id="eq-val">${val}</span>`;
    const helpEl = document.getElementById('eq-help');
    helpEl.textContent = vocalEqHelp[val] || '';
}

for (const radio of modeRadios) {
    radio.addEventListener('change', updateModeUI);
}

// Initialize mode UI
updateModeUI();

// --- Dynamic help text maps ---
const sensitivityHelpMap = {
    1: 'Molto selettivo: solo voce fortissima',
    2: 'Selettivo: voce molto forte',
    3: 'Abbastanza selettivo: voce medio-alta',
    4: 'Medio-selettivo: voce di media intensit\u00e0',
    5: 'Bilanciato: rileva voce di media intensit\u00e0',
    6: 'Sensibile: rileva anche voce leggera',
    7: 'Abbastanza sensibile: rileva voce debole',
    8: 'Molto sensibile: rileva quasi tutto',
    9: 'Ultra sensibile: rileva anche residui minimi',
    10: 'Massima sensibilit\u00e0: rileva tutto',
};

const vocalEqHelp = {
    0: 'Disattivato: nessuna compensazione',
    1: 'Minimo: compensazione appena percettibile',
    2: 'Leggero: compensazione sottile',
    3: 'Leggero-medio: riempie leggermente le frequenze perse',
    4: 'Medio-leggero: compensazione moderata',
    5: 'Medio: compensa le frequenze perse con buon bilanciamento',
    6: 'Medio-forte: compensazione evidente',
    7: 'Forte: compensazione marcata',
    8: 'Molto forte: compensazione aggressiva',
    9: 'Aggressivo: compensazione molto marcata',
    10: 'Massimo: compensazione massima',
};

function bindSlider(slider, displayId, helpId, helpMap) {
    const display = document.getElementById(displayId);
    const helpEl = helpId ? document.getElementById(helpId) : null;
    if (!display) return;
    slider.addEventListener('input', () => {
        display.textContent = slider.value;
        if (helpEl && helpMap) {
            const key = parseInt(slider.value);
            helpEl.textContent = helpMap[key] || '';
        }
    });
}

function bindSliderFn(slider, displayId, helpId, helpFn) {
    const display = document.getElementById(displayId);
    const helpEl = helpId ? document.getElementById(helpId) : null;
    if (!display) return;
    slider.addEventListener('input', () => {
        display.textContent = slider.value;
        if (helpEl && helpFn) {
            helpEl.textContent = helpFn(parseInt(slider.value));
        }
    });
}

bindSlider(sensitivitySlider, 'sensitivity-val', 'sensitivity-help', sensitivityHelpMap);
bindSliderFn(bandCountSlider, 'band-count-val', 'band-count-help',
    v => `${v} bande logaritmiche da 60Hz a 16kHz${v <= 8 ? ' (bassa risoluzione)' : v <= 16 ? ' (buona risoluzione)' : v <= 24 ? ' (alta risoluzione)' : ' (massima risoluzione)'}`);

// EQ slider: dynamic behavior based on mode
eqSlider.addEventListener('input', () => {
    if (getMode() === 'mix') {
        updateEqForMixMode();
    } else {
        updateEqForVocalMode();
    }
});

// --- Enable analyze when both files selected ---
function checkFiles() {
    analyzeBtn.disabled = !(vocalInput.files.length && instInput.files.length);
}
vocalInput.addEventListener('change', checkFiles);
instInput.addEventListener('change', checkFiles);

// --- Analyze (SSE for progress) ---
const analyzeProgressContainer = document.getElementById('analyze-progress-container');
const analyzeProgressFill = document.getElementById('analyze-progress-fill');
const analyzeProgressText = document.getElementById('analyze-progress-text');

analyzeBtn.addEventListener('click', async () => {
    const isReanalyze = !!sessionId;

    analyzeBtn.disabled = true;
    analyzeProgressContainer.classList.remove('hidden');
    analyzeProgressFill.style.width = '0%';
    analyzeProgressText.textContent = '0%';
    analyzeStatus.textContent = '';

    // Hide result section on re-analysis
    document.getElementById('result-section').classList.add('hidden');
    progressContainer.classList.add('hidden');
    progressFill.style.width = '0%';
    progressText.textContent = '0%';

    if (isReanalyze) {
        await doReanalyze();
    } else {
        await doFirstAnalyze();
    }
});

async function doFirstAnalyze() {
    const formData = new FormData();
    formData.append('vocal', vocalInput.files[0]);
    formData.append('instrumental', instInput.files[0]);
    formData.append('sensitivity', sensitivitySlider.value);
    formData.append('band_count', bandCountSlider.value);
    formData.append('mode', getMode());

    try {
        const resp = await fetch('/api/analyze', { method: 'POST', body: formData });
        if (!resp.ok) {
            const err = await resp.text();
            throw new Error(err || resp.statusText);
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let eventType = null;

        function processLines(lines) {
            for (const line of lines) {
                if (line.startsWith('event: ')) {
                    eventType = line.slice(7).trim();
                } else if (line.startsWith('data: ') && eventType) {
                    const data = JSON.parse(line.slice(6));
                    if (eventType === 'progress') {
                        analyzeProgressFill.style.width = `${data.percent}%`;
                        analyzeProgressText.textContent = `${data.percent}%`;
                        analyzeStatus.textContent = data.step;
                    } else if (eventType === 'result') {
                        analysisData = data;
                        sessionId = data.session_id;
                    }
                    eventType = null;
                }
            }
        }

        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                if (buffer.trim()) processLines(buffer.split('\n'));
                break;
            }
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            processLines(lines);
        }

        if (!analysisData) throw new Error('Nessun risultato ricevuto');

        analyzeStatus.textContent = 'Analisi completata.';
        analyzeProgressFill.style.width = '100%';
        analyzeProgressText.textContent = '100%';
        showWaveforms();
        analyzeBtn.disabled = false;
        analyzeBtn.textContent = 'Ri-analizza';
    } catch (e) {
        analyzeStatus.textContent = `Errore: ${e.message}`;
        analyzeBtn.disabled = false;
        analyzeBtn.textContent = 'Analizza';
    }
}

async function doReanalyze() {
    const formData = new FormData();
    formData.append('session_id', sessionId);
    formData.append('sensitivity', sensitivitySlider.value);
    formData.append('band_count', bandCountSlider.value);
    formData.append('mode', getMode());

    try {
        analyzeStatus.textContent = 'Ri-analisi in corso...';
        analyzeProgressFill.style.width = '50%';
        analyzeProgressText.textContent = '50%';

        const resp = await fetch('/api/reanalyze', { method: 'POST', body: formData });
        if (!resp.ok) {
            let errMsg = resp.statusText;
            try { errMsg = (await resp.json()).detail || errMsg; } catch { errMsg = await resp.text() || errMsg; }
            throw new Error(errMsg);
        }
        analysisData = await resp.json();

        analyzeStatus.textContent = 'Ri-analisi completata.';
        analyzeProgressFill.style.width = '100%';
        analyzeProgressText.textContent = '100%';
        drawHeatmap();
        updateStats();

        analyzeBtn.disabled = false;
        processBtn.disabled = false;
    } catch (e) {
        analyzeStatus.textContent = `Errore: ${e.message}`;
        analyzeBtn.disabled = false;
    }
}

// --- Waveform display ---
function showWaveforms() {
    document.getElementById('waveform-section').classList.remove('hidden');
    document.getElementById('process-section').classList.remove('hidden');

    if (wsVocal) wsVocal.destroy();
    if (wsInstrumental) wsInstrumental.destroy();

    const vocalUrl = `/api/audio/${sessionId}/vocal`;
    const instUrl = `/api/audio/${sessionId}/instrumental`;

    wsVocal = WaveSurfer.create({
        container: '#waveform-vocal',
        waveColor: '#8888cc',
        progressColor: '#5555aa',
        height: 80,
        barWidth: 2,
        barGap: 1,
        barRadius: 2,
        url: vocalUrl,
        backend: 'MediaElement',
    });

    wsInstrumental = WaveSurfer.create({
        container: '#waveform-instrumental',
        waveColor: '#4ecdc4',
        progressColor: '#36b5ad',
        height: 80,
        barWidth: 2,
        barGap: 1,
        barRadius: 2,
        url: instUrl,
        backend: 'MediaElement',
    });

    wsInstrumental.on('ready', () => {
        drawHeatmap();
        updateStats();
    });

    // Sync playback
    wsVocal.on('interaction', () => {
        wsInstrumental.setTime(wsVocal.getCurrentTime());
    });
    wsInstrumental.on('interaction', () => {
        wsVocal.setTime(wsInstrumental.getCurrentTime());
    });

    processBtn.disabled = false;
}

// --- Heatmap drawing ---
function drawHeatmap() {
    if (!analysisData) return;

    const canvas = document.getElementById('heatmap-canvas');
    const ctx = canvas.getContext('2d');
    const heatmap = analysisData.intensity_heatmap;
    const bands = analysisData.bands;
    const nBands = heatmap.length;
    const nFrames = heatmap[0].length;

    const pixelsPerBand = 8;
    canvas.width = nFrames;
    canvas.height = nBands * pixelsPerBand;

    // Draw heatmap (low frequencies at bottom, high at top)
    for (let b = 0; b < nBands; b++) {
        for (let t = 0; t < nFrames; t++) {
            const intensity = heatmap[b][t];
            ctx.fillStyle = intensityToColor(intensity);
            ctx.fillRect(t, (nBands - 1 - b) * pixelsPerBand, 1, pixelsPerBand);
        }
    }

    // Update band labels using safe DOM methods
    const labelsEl = document.getElementById('heatmap-bands-labels');
    while (labelsEl.firstChild) {
        labelsEl.removeChild(labelsEl.firstChild);
    }

    // Show representative band labels
    const labelIndices = [];
    if (nBands <= 8) {
        for (let i = 0; i < nBands; i++) labelIndices.push(i);
    } else {
        const step = Math.max(1, Math.floor(nBands / 5));
        for (let i = 0; i < nBands; i += step) labelIndices.push(i);
        if (labelIndices[labelIndices.length - 1] !== nBands - 1) labelIndices.push(nBands - 1);
    }
    for (const i of labelIndices) {
        const span = document.createElement('span');
        span.className = 'heatmap-band-label';
        span.textContent = formatFreq(bands[i].center_hz);
        const pct = ((i + 0.5) / nBands) * 100;
        span.style.bottom = `${pct}%`;
        labelsEl.appendChild(span);
    }
}

function intensityToColor(value) {
    if (value <= 0) return '#1a1a2e';
    if (value < 0.25) {
        return lerpColor([26, 26, 46], [0, 80, 160], value / 0.25);
    }
    if (value < 0.5) {
        return lerpColor([0, 80, 160], [0, 200, 200], (value - 0.25) / 0.25);
    }
    if (value < 0.75) {
        return lerpColor([0, 200, 200], [255, 200, 0], (value - 0.5) / 0.25);
    }
    return lerpColor([255, 200, 0], [233, 69, 96], (value - 0.75) / 0.25);
}

function lerpColor(a, b, t) {
    const r = Math.round(a[0] + (b[0] - a[0]) * t);
    const g = Math.round(a[1] + (b[1] - a[1]) * t);
    const bl = Math.round(a[2] + (b[2] - a[2]) * t);
    return `rgb(${r},${g},${bl})`;
}

function formatFreq(hz) {
    if (hz >= 1000) return `${(hz / 1000).toFixed(1)}kHz`;
    return `${Math.round(hz)}Hz`;
}

function updateStats() {
    if (!analysisData) return;
    const heatmap = analysisData.intensity_heatmap;
    const nBands = heatmap.length;
    const nFrames = heatmap[0].length;

    let activeCells = 0;
    let totalIntensity = 0;
    for (let b = 0; b < nBands; b++) {
        for (let t = 0; t < nFrames; t++) {
            if (heatmap[b][t] > 0) {
                activeCells++;
                totalIntensity += heatmap[b][t];
            }
        }
    }
    const totalCells = nBands * nFrames;
    const coveragePct = Math.round(activeCells / totalCells * 100);
    const avgIntensity = activeCells > 0 ? (totalIntensity / activeCells * 100).toFixed(0) : 0;

    document.getElementById('segment-stats').textContent =
        `${nBands} bande \u00d7 ${nFrames} frame | ` +
        `Copertura: ${coveragePct}% | ` +
        `Intensit\u00e0 media zone attive: ${avgIntensity}%`;
}

// --- Process ---
processBtn.addEventListener('click', async () => {
    if (!sessionId) return;
    processBtn.disabled = true;
    progressContainer.classList.remove('hidden');
    progressFill.style.width = '0%';
    progressText.textContent = '0%';

    const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${wsProtocol}//${location.host}/ws/progress/${sessionId}`);
    ws.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.type === 'progress') {
            progressFill.style.width = `${data.percent}%`;
            progressText.textContent = `${data.percent}%`;
        }
    };

    try {
        const resp = await fetch('/api/process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: sessionId,
                mode: getMode(),
                eq_level: parseInt(eqSlider.value),
                band_count: parseInt(bandCountSlider.value),
                sensitivity: parseInt(sensitivitySlider.value),
                stereo_widen: stereoCheck.checked,
                normalization: normSelect.value,
            }),
        });

        if (!resp.ok) {
            let errMsg = resp.statusText;
            try { errMsg = (await resp.json()).detail || errMsg; } catch { errMsg = await resp.text() || errMsg; }
            throw new Error(errMsg);
        }

        const result = await resp.json();

        progressFill.style.width = '100%';
        progressText.textContent = '100%';

        showResult(result);
    } catch (e) {
        progressText.textContent = `Errore: ${e.message}`;
    } finally {
        ws.close();
        processBtn.disabled = false;
    }
});

// --- Result ---
function showResult(result) {
    document.getElementById('result-section').classList.remove('hidden');

    const originalAudio = document.getElementById('audio-original');
    const processedAudio = document.getElementById('audio-processed');

    originalAudio.src = `/api/audio/${sessionId}/instrumental`;
    processedAudio.src = `/api/download/${sessionId}/${result.output_filename}`;

    downloadBtn.onclick = () => {
        const a = document.createElement('a');
        a.href = `/api/download/${sessionId}/${result.output_filename}`;
        a.download = result.output_filename;
        a.click();
    };
}
