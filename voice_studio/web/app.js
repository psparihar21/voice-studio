"use strict";
(function(){
  var state = {takes:{}, selected:{}, roomtone:false}, recPeak = 0, savedIdx = 0, asBusy = false;
  var ctx = null, stream = null, node = null, analyser = null, capturing = false, chunks = [], recStartT = 0, timerH = null, counting = false, kind = 'full', offsetSec = 0, discard = false, stopTimer = null, aPaused = false, vPaused = false, vByAudio = false, samples = 0, segs = [], levelOk = false, wake = null, pollH = null;
  var $ = function(id){return document.getElementById(id)};
  var player = $('player'), pv = $('pv');
  /* every request carries the app header: the studio refuses POSTs without it, which blocks other websites */
  function xf(url, opt){ opt = opt || {}; opt.headers = Object.assign({'X-Voice-Studio': '1'}, opt.headers || {}); return fetch(url, opt); }
  function api(path, opt){ return xf(path, opt).then(function(r){ return r.json(); }); }
  var info = {capabilities: {ffmpeg: true, transcribe: true, clean: true}, sets: [], preferred_mic: ''};
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(m){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]; }); }
  function mmss(s){ var m = Math.floor(s / 60), x = Math.floor(s % 60); return m + ':' + (x < 10 ? '0' : '') + x; }
  function store(k, v){ try{ if (v === undefined) return localStorage.getItem('vs_' + k); localStorage.setItem('vs_' + k, v); }catch(e){ return null; } }
  function say(html){ $('feedback').innerHTML = html; }

  /* ---------- takes ---------- */
  function load(){ return api('/api/state').then(function(s){ state = s; renderTakes(); renderCheck(); schedulePoll(); }); }
  function schedulePoll(){
    var busy = (state.takes.full || []).some(function(k){ return k.job === 'running' || k.clean === 'running'; });
    clearTimeout(pollH); if (busy) pollH = setTimeout(load, 3000);
  }
  function renderCheck(){
    $('ck1').className = stream ? 'ok' : ''; $('ck2').className = levelOk ? 'ok' : ''; $('ck3').className = state.roomtone ? 'ok' : '';
    $('readyCard').hidden = !!(stream && levelOk);
  }
  function takeDate(t){ if (!t) return ''; var d = new Date(t * 1000); return d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}); }
  function renderTakes(){
    var t = state.takes.full || [], sel = state.selected.full;
    if (!t.length){ $('takes').innerHTML = '<div class="note">No takes yet. Your first recording will appear here.</div>'; return; }
    $('takes').innerHTML = t.slice().reverse().map(function(k){
      var pk = k.peak ? (20 * Math.log10(k.peak)).toFixed(0) + ' dBFS peak' : '';
      var caps = info.capabilities;
      var script = !caps.transcribe && !k.script ? '' : k.job === 'running' ? '<button data-t="' + k.file + '"><span class="spin"></span>Writing script…</button>' :
        k.job === 'error' ? '<button data-t="' + k.file + '" title="' + esc(k.jobmsg) + '">Retry script</button>' :
        k.script ? '<button data-t="' + k.file + '" class="primary">📄 Script</button><button data-r="' + k.file + '">Read</button>' :
        '<button data-t="' + k.file + '">📄 Script</button>';
      var cl = k.clean === 'done' ? '<span class="badge ok" title="Noise removed, voice kept natural. The original is untouched.">voice cleaned</span>' : k.clean === 'running' ? '<span class="badge run"><span class="spin"></span>cleaning voice</span>' : '';
      var clBtn = !caps.clean && k.clean !== 'done' ? '' : k.clean === 'done' ? '<button data-po="' + k.file + '" title="Hear the untouched recording">▶ Original</button>' : k.clean === 'running' ? '' : '<button data-cl="' + k.file + '" title="' + esc(k.cleanmsg || 'Remove noise and polish the voice') + '">' + (k.clean === 'error' ? 'Retry cleanup' : '✨ Clean voice') + '</button>';
      return '<div class="take' + (k.n === sel ? ' sel' : '') + '"><div class="h"><b>Take ' + k.n + '</b>' + (k.n === sel ? '<span class="badge ok">in use</span>' : '') + cl +
        '<span class="m">' + (k.sec ? mmss(k.sec) : '') + (k.segs && k.segs[0] && k.segs[0].vid > 1 ? ' · from ' + mmss(k.segs[0].vid) : '') + (takeDate(k.mtime) ? ' · ' + takeDate(k.mtime) : '') + (pk ? ' · ' + pk : '') + '</span></div>' +
        '<canvas data-wf="' + k.file + '" aria-label="Waveform of take ' + k.n + '"></canvas>' +
        '<div class="btns"><button data-p="' + k.file + '"' + (k.clean === 'done' ? ' data-c="1"' : '') + '>▶ Listen</button>' + clBtn + '<button data-w="' + k.file + '" data-o="' + (k.offset || 0) + '"' + (k.clean === 'done' ? ' data-c="1"' : '') + '>▶ With picture</button>' + script +
        (k.n === sel ? '' : '<button data-s="' + k.n + '">Use this one</button>') + '<button data-d="' + k.file + '">Delete</button></div></div>';
    }).join('');
    Array.prototype.forEach.call($('takes').querySelectorAll('button'), function(b){
      b.onclick = function(){
        if (b.dataset.p) { stopPlayback(); player.src = (b.dataset.c ? '/rec/clean/' : '/rec/') + b.dataset.p + '?t=' + Date.now(); player.play(); }
        else if (b.dataset.po) { stopPlayback(); player.src = '/rec/' + b.dataset.po + '?t=' + Date.now(); player.play(); }
        else if (b.dataset.cl) { api('/api/clean?file=' + b.dataset.cl, {method:'POST'}).then(load); }
        else if (b.dataset.w) { watchWith(b.dataset.w, parseFloat(b.dataset.o) || 0, !!b.dataset.c); }
        else if (b.dataset.t) { getScript(b.dataset.t); }
        else if (b.dataset.r) { readScript(b.dataset.r); }
        else if (b.dataset.s) { api('/api/select?cue=full&n=' + b.dataset.s, {method:'POST'}).then(load); }
        else if (b.dataset.d) { if (confirm('Delete this take? You can undo right after.')) del(b.dataset.d); }
      };
    });
    Array.prototype.forEach.call($('takes').querySelectorAll('canvas[data-wf]'), function(c){ drawWave(c, c.dataset.wf); });
  }
  var waveCache = {};
  function drawWave(c, file){
    function paint(peaks){
      var w = c.clientWidth || 340, h = 42, dpr = window.devicePixelRatio || 1; c.width = w * dpr; c.height = h * dpr;
      var g = c.getContext('2d'); g.scale(dpr, dpr); g.clearRect(0, 0, w, h); g.fillStyle = '#7fb2ff';
      var n = peaks.length, bw = w / n;
      for (var i = 0; i < n; i++){ var bh = Math.max(1.5, peaks[i] * (h - 4)); g.fillRect(i * bw, (h - bh) / 2, Math.max(1, bw - 0.6), bh); }
    }
    if (waveCache[file]) return paint(waveCache[file]);
    xf('/rec/' + file).then(function(r){ return r.arrayBuffer(); }).then(function(ab){
      var ac = new (window.AudioContext || window.webkitAudioContext)();
      return ac.decodeAudioData(ab).then(function(buf){
        var d = buf.getChannelData(0), N = 160, step = Math.floor(d.length / N) || 1, out = [], mx = 0.0001;
        for (var i = 0; i < N; i++){ var m = 0; for (var j = i * step; j < Math.min(d.length, (i + 1) * step); j += 8){ var a = Math.abs(d[j]); if (a > m) m = a; } out.push(m); if (m > mx) mx = m; }
        waveCache[file] = out.map(function(v){ return v / mx; }); try{ ac.close(); }catch(e){}
        if (document.body.contains(c)) paint(waveCache[file]);
      });
    }).catch(function(){});
  }
  function stopPlayback(){ withAudio = null; try{ player.pause(); }catch(e){} try{ pv.pause(); }catch(e){} }
  function del(file){
    stopPlayback(); player.removeAttribute('src'); player.load();    // release the file before deleting it
    api('/api/delete?file=' + file, {method:'POST'}).then(function(r){
      if (r.error) say('<span class="bad">' + esc(r.error) + '</span>');
      else { say('<span class="good">Moved to trash.</span> <button id="undoBtn">Undo</button>');
        $('undoBtn').onclick = function(){ api('/api/restore?file=' + file, {method:'POST'}).then(function(x){ say(x.ok ? '<span class="good">Restored as Take ' + x.n + '.</span>' : '<span class="bad">Could not restore.</span>'); return load(); }); }; }
      return load();
    }).catch(function(){ say('<span class="bad">Could not reach the studio. Is it still running?</span>'); });
  }
  var withAudio = null;   // {off} while a take is being watched with the picture
  function watchWith(file, off, clean){
    stopPlayback(); pv.currentTime = 0; player.src = (clean ? '/rec/clean/' : '/rec/') + file + '?t=' + Date.now(); player.currentTime = 0; withAudio = {off: off};
    player.play().then(function(){ setTimeout(function(){ if (withAudio) pv.play(); }, Math.max(0, off * 1000)); });
  }
  function syncAudio(){ if (!withAudio) return; try{ player.currentTime = Math.max(0, pv.currentTime - withAudio.off); }catch(e){} }
  function togglePlay(){
    if (capturing || counting) return;
    if (pv.paused){ if (pv.ended) pv.currentTime = 0; pv.play(); if (withAudio){ syncAudio(); player.play(); } }
    else { pv.pause(); if (withAudio) player.pause(); }
  }
  /* while recording, a backwards jump (only allowed when paused) flags the slip at the old position, like Redo */
  function seekLog(prev){
    if (!capturing) return;
    if (pv.currentTime < prev - 0.5){ evt('redo', null, prev); mark(); }
    evt('video_seek');
  }
  function seekBy(d){ if (capturing && !aPaused) return; var prev = pv.currentTime; pv.currentTime = Math.max(0, Math.min(pv.duration || 0, prev + d)); syncAudio(); seekLog(prev); }
  function tpUpdate(){
    var d = pv.duration || 0, t = pv.currentTime || 0;
    if (!seeking) $('seek').value = d ? Math.round(1000 * t / d) : 0;
    $('tpTime').textContent = mmss(t) + ' / ' + mmss(d);
    $('playBtn').textContent = pv.paused ? '▶' : '⏸';
  }
  var seeking = false, seekPrev = 0;
  $('seek').oninput = function(){ if (!seeking) seekPrev = pv.currentTime; seeking = true; if (pv.duration) pv.currentTime = pv.duration * $('seek').value / 1000; $('tpTime').textContent = mmss(pv.currentTime) + ' / ' + mmss(pv.duration || 0); };
  $('seek').onchange = function(){ seeking = false; syncAudio(); seekLog(seekPrev); };
  $('playBtn').onclick = togglePlay;
  pv.onclick = togglePlay;
  ['timeupdate','play','pause','loadedmetadata','seeked','ended'].forEach(function(n){ pv.addEventListener(n, tpUpdate); });

  /* ---------- script (transcript) ---------- */
  function saveScript(file){ var a = document.createElement('a'); a.href = '/script/' + file.replace('.wav', '') + '.txt'; a.download = ''; document.body.appendChild(a); a.click(); a.remove(); }
  function getScript(file){
    api('/api/transcribe_status?file=' + file).then(function(j){
      if (j.state === 'done'){ say('<span class="good">Script downloaded.</span>'); saveScript(file); return; }
      if (j.state !== 'running') api('/api/transcribe?file=' + file, {method:'POST'});
      say('<span class="warn">Still writing the script. It downloads by itself when ready.</span>');
      (function poll(){
        api('/api/transcribe_status?file=' + file).then(function(x){
          if (x.state === 'done'){ say('<span class="good">Script ready. Downloading.</span>'); saveScript(file); load(); }
          else if (x.state === 'error'){ say('<span class="bad">Could not write the script: ' + esc(x.msg) + '</span>'); load(); }
          else setTimeout(poll, 2000);
        });
      })();
      load();
    });
  }
  var curScript = null, panelFs = parseInt(store('fs')) || 15, panel = $('scriptPanel'), tab = 'mine', txt = $('scriptText'), notesTimer = null, notesLoaded = false, takeText = '', takeDirty = false, scrollRaf = null;
  function clampPanel(){
    var l = Math.min(Math.max(0, panel.offsetLeft), Math.max(0, innerWidth - 120)), t = Math.min(Math.max(0, panel.offsetTop), Math.max(0, innerHeight - 44));
    panel.style.left = l + 'px'; panel.style.top = t + 'px';
  }
  function savePanel(){ if (!panel.offsetWidth) return; store('pnl', JSON.stringify({l: panel.offsetLeft, t: panel.offsetTop, w: panel.offsetWidth, h: panel.offsetHeight})); }
  function applyFs(){ panel.style.setProperty('--fs', panelFs + 'px'); store('fs', String(panelFs)); }
  function note(t){ $('saveNote').textContent = t; }
  function showPanel(){
    if (!panel.hidden) return;
    try{ var p = JSON.parse(store('pnl') || 'null'); if (p && (p.l > 0 || p.t > 0)){ panel.style.left = p.l + 'px'; panel.style.top = p.t + 'px'; panel.style.width = p.w + 'px'; panel.style.height = p.h + 'px'; } }catch(e){}
    var o = parseInt(store('op')); if (o){ $('fOp').value = o; panel.style.opacity = o / 100; }
    $('scrollRec').checked = store('scrollRec') === '1'; var sp = parseInt(store('spd')); if (sp) $('scrollSpd').value = sp;
    panel.hidden = false; applyFs(); clampPanel();
  }
  function setTab(t){
    tab = t; $('tabMine').classList.toggle('on', t === 'mine'); $('tabTake').classList.toggle('on', t === 'take');
    $('saveTake').hidden = t !== 'take'; $('dlBtn').hidden = t !== 'take'; note('');
    if (t === 'mine'){ txt.value = notesLoaded ? (txt.dataset.mine || '') : ''; loadNotes(); }
    else { txt.value = takeText; }
  }
  function loadNotes(){
    api('/api/notes').then(function(r){ notesLoaded = true; txt.dataset.mine = r.text || ''; if (tab === 'mine') txt.value = txt.dataset.mine; });
  }
  function openMine(){ showPanel(); setTab('mine'); }
  function readScript(file){
    xf('/script/' + file.replace('.wav', '') + '.txt').then(function(r){ return r.text(); }).then(function(t){
      curScript = file; takeText = t.replace(/\r/g, ''); takeDirty = false; $('tabTake').textContent = 'Take ' + file.replace('.wav', '').replace('full_t', '') + ' script';
      showPanel(); setTab('take');
    });
  }
  txt.addEventListener('input', function(){
    if (tab === 'mine'){
      txt.dataset.mine = txt.value; note('Saving…'); clearTimeout(notesTimer);
      notesTimer = setTimeout(function(){ xf('/api/notes', {method:'POST', body: txt.value}).then(function(){ note('Saved'); }).catch(function(){ note('Not saved'); }); }, 700);
    } else { takeText = txt.value; takeDirty = true; note('Edited, press Save edit'); }
  });
  txt.addEventListener('keydown', function(e){ if (e.key === 'Escape') txt.blur(); });
  $('saveTake').onclick = function(){
    if (!curScript) return;
    xf('/api/script_save?file=' + curScript, {method:'POST', body: txt.value}).then(function(r){ return r.json(); }).then(function(r){ note(r.ok ? 'Saved. The download uses this version.' : 'Could not save'); takeDirty = false; });
  };
  $('tabMine').onclick = function(){ setTab('mine'); };
  $('tabTake').onclick = function(){ if (curScript) setTab('take'); else note('Press Read on a take first'); };
  $('myScriptBtn').onclick = openMine;
  $('scriptClose').onclick = function(){ stopScroll(); panel.hidden = true; };
  $('fMinus').onclick = function(){ panelFs = Math.max(11, panelFs - 1); applyFs(); };
  $('fPlus').onclick = function(){ panelFs = Math.min(40, panelFs + 1); applyFs(); };
  $('fOp').oninput = function(){ panel.style.opacity = $('fOp').value / 100; store('op', $('fOp').value); };
  /* teleprompter */
  function startScroll(){
    if (scrollRaf) return; var last = performance.now(); $('scrollBtn').textContent = '⏸ Stop scroll';
    (function step(now){ var dt = (now - last) / 1000; last = now; txt.scrollTop += parseInt($('scrollSpd').value) * dt; if (txt.scrollTop + txt.clientHeight >= txt.scrollHeight - 1){ stopScroll(); return; } scrollRaf = requestAnimationFrame(step); })(last);
  }
  function stopScroll(){ if (scrollRaf) cancelAnimationFrame(scrollRaf); scrollRaf = null; $('scrollBtn').textContent = '▶ Auto-scroll'; }
  $('scrollBtn').onclick = function(){ if (scrollRaf) stopScroll(); else { if (txt.scrollTop + txt.clientHeight >= txt.scrollHeight - 1) txt.scrollTop = 0; startScroll(); } };
  $('scrollSpd').oninput = function(){ store('spd', $('scrollSpd').value); };
  $('scrollRec').onchange = function(){ store('scrollRec', $('scrollRec').checked ? '1' : '0'); };
  window.vsOnRecordStart = function(){ if (!panel.hidden && $('scrollRec').checked && !$('followVid').checked){ txt.scrollTop = 0; startScroll(); } };
  window.vsOnRecordStop = function(){ stopScroll(); };
  (function(){
    var drag = null, bar = $('scriptDrag');
    bar.addEventListener('pointerdown', function(e){
      if (e.target.closest('button, input')) return;
      drag = {dx: e.clientX - panel.offsetLeft, dy: e.clientY - panel.offsetTop}; try{ bar.setPointerCapture(e.pointerId); }catch(err){}
    });
    bar.addEventListener('pointermove', function(e){ if (!drag) return; panel.style.left = (e.clientX - drag.dx) + 'px'; panel.style.top = (e.clientY - drag.dy) + 'px'; clampPanel(); });
    bar.addEventListener('pointerup', function(){ if (drag){ drag = null; savePanel(); } });
    try{ new ResizeObserver(function(){ if (!panel.hidden) savePanel(); }).observe(panel); }catch(e){}
    window.addEventListener('resize', function(){ if (!panel.hidden) clampPanel(); });
  })();
  $('dlBtn').onclick = function(){ if (curScript) saveScript(curScript); };
  $('copyBtn').onclick = function(){ try{ navigator.clipboard.writeText(txt.value); $('copyBtn').textContent = 'Copied'; setTimeout(function(){ $('copyBtn').textContent = 'Copy'; }, 1500); }catch(e){} };
  Array.prototype.forEach.call(document.querySelectorAll('.closeBtn'), function(b){ b.onclick = function(){ b.closest('dialog').close(); }; });
  $('helpBtn').onclick = function(){ $('helpDlg').showModal(); };

  /* ---------- microphone ---------- */
  function setMicUi(on, label){
    $('micPill').className = 'pill' + (on ? ' ok' : ''); $('micTxt').textContent = label;
    $('micBtn').hidden = on; $('micSel').hidden = !on; $('testBtn').hidden = !on; renderCheck();
  }
  function startMic(deviceId){
    if (stream){ stream.getTracks().forEach(function(t){ t.stop(); }); }
    var cons = {audio:{echoCancellation:false, noiseSuppression:false, autoGainControl:false, channelCount:1}};
    if (deviceId) cons.audio.deviceId = {exact: deviceId};
    return navigator.mediaDevices.getUserMedia(cons).then(function(s){
      stream = s;
      if (ctx) { try{ ctx.close(); }catch(e){} }
      ctx = new (window.AudioContext || window.webkitAudioContext)({sampleRate:48000}); try{ ctx.resume(); }catch(e){}
      return ctx.audioWorklet.addModule('/static/worklet.js').then(function(){
        var src = ctx.createMediaStreamSource(stream);
        analyser = ctx.createAnalyser(); analyser.fftSize = 1024;
        node = new AudioWorkletNode(ctx, 'rec');
        node.port.onmessage = function(e){
          if (!capturing || aPaused) return;
          var c = e.data, q = new Int16Array(c.length);
          for (var i = 0; i < c.length; i++){ var x = Math.max(-1, Math.min(1, c[i])), a = x < 0 ? -x : x; if (a > recPeak) recPeak = a; q[i] = x < 0 ? x * 32768 : x * 32767; }
          chunks.push(q); samples += c.length;
        };
        src.connect(analyser); src.connect(node);
        return navigator.mediaDevices.enumerateDevices();
      });
    }).then(function(devs){
      var sel = $('micSel'), ins = devs.filter(function(d){ return d.kind === 'audioinput'; });
      var curId = stream.getAudioTracks()[0].getSettings().deviceId;
      sel.innerHTML = ins.map(function(d,i){ return '<option value="' + d.deviceId + '">' + esc(d.label || ('Microphone ' + (i+1))) + '</option>'; }).join('');
      sel.value = curId;
      var saved = store('mic'), pref = ins.filter(function(d){ if (saved) return d.deviceId === saved; try{ return !!info.preferred_mic && new RegExp(info.preferred_mic, 'i').test(d.label); }catch(e){ return false; } })[0];
      if (pref && !deviceId && pref.deviceId !== curId){ sel.value = pref.deviceId; return startMic(pref.deviceId); }
      var label = (sel.options[sel.selectedIndex] || {}).text || 'Microphone';
      setMicUi(true, label.replace(/\s*\(.*\)\s*$/, '')); meterLoop();
    }).catch(function(err){
      setMicUi(false, 'Microphone off'); $('meterTxt').innerHTML = '&nbsp;';
      say('<span class="bad">The browser could not open the microphone (' + esc(err.name || err) + '). Allow the microphone for this page, then press Enable microphone again.</span>');
    });
  }
  var peakHold = 0, peakAt = 0, meterOn = false;
  function meterLoop(){
    if (!analyser) return; if (meterOn) return; meterOn = true;
    (function tick(){
      var buf = new Float32Array(analyser.fftSize); analyser.getFloatTimeDomainData(buf);
      var pk = 0; for (var i = 0; i < buf.length; i++){ var a = Math.abs(buf[i]); if (a > pk) pk = a; }
      var db = pk > 0 ? 20 * Math.log10(pk) : -90, now = performance.now();
      if (db > peakHold || now - peakAt > 1200){ peakHold = db; peakAt = now; }
      var f = $('meterFill'); f.style.width = Math.max(0, Math.min(100, (db + 60) / 60 * 100)) + '%'; f.style.background = db > -3 ? 'var(--rec)' : db > -9 ? 'var(--warn)' : 'var(--ok)';
      $('meterPeak').style.left = Math.max(0, Math.min(100, (peakHold + 60) / 60 * 100)) + '%';
      $('meterTxt').textContent = ctx && ctx.state === 'suspended' ? 'click anywhere' : (db < -80 ? 'silent' : db.toFixed(0) + ' dBFS');
      requestAnimationFrame(tick);
    })();
  }
  ['pointerdown', 'keydown'].forEach(function(n){ document.addEventListener(n, function(){ if (ctx && ctx.state === 'suspended') ctx.resume(); }, true); });
  $('micBtn').onclick = function(){ startMic(null); };
  $('micSel').onchange = function(){ store('mic', $('micSel').value); meterOn = false; startMic($('micSel').value); };
  function testMic(){
    if (!analyser || capturing) return;
    var b = $('testBtn'), n = 0, mx = -90; b.disabled = true;
    say('<span class="warn">Say a sentence in your normal voice for 4 seconds…</span>');
    var h = setInterval(function(){
      var buf = new Float32Array(analyser.fftSize); analyser.getFloatTimeDomainData(buf);
      var pk = 0; for (var i = 0; i < buf.length; i++){ var a = Math.abs(buf[i]); if (a > pk) pk = a; }
      var db = pk > 0 ? 20 * Math.log10(pk) : -90; if (db > mx) mx = db;
      if (++n >= 40){ clearInterval(h); b.disabled = false; levelOk = mx >= -24 && mx <= -2; renderCheck();
        say(mx < -45 ? '<span class="bad">I hear almost nothing (' + mx.toFixed(0) + ' dBFS). Check the mic choice or your Windows input volume.</span>' :
          mx < -24 ? '<span class="warn">Quiet (peak ' + mx.toFixed(0) + ' dBFS). Move closer or raise the input volume.</span>' :
          mx > -2 ? '<span class="bad">Too loud, it will clip (peak ' + mx.toFixed(0) + ' dBFS). Lower the input volume.</span>' :
          '<span class="good">Mic is good (peak ' + mx.toFixed(0) + ' dBFS). You can record.</span>'); }
    }, 100);
  }
  $('testBtn').onclick = testMic;

  /* ---------- recording ---------- */
  function encodeWav(parts, sr){
    var n = 0; parts.forEach(function(p){ n += p.length; });
    var buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf), o = 0;
    function s(str){ for (var i = 0; i < str.length; i++) v.setUint8(o++, str.charCodeAt(i)); }
    s('RIFF'); v.setUint32(o, 36 + n * 2, true); o += 4; s('WAVEfmt '); v.setUint32(o, 16, true); o += 4;
    v.setUint16(o, 1, true); o += 2; v.setUint16(o, 1, true); o += 2; v.setUint32(o, sr, true); o += 4; v.setUint32(o, sr * 2, true); o += 4;
    v.setUint16(o, 2, true); o += 2; v.setUint16(o, 16, true); o += 2; s('data'); v.setUint32(o, n * 2, true); o += 4;
    var pcm = new Int16Array(buf, 44, n), at = 0;
    parts.forEach(function(p){ pcm.set(p, at); at += p.length; });
    return {blob: new Blob([buf], {type:'audio/wav'}), sec: n / sr, peak: recPeak};
  }
  function countdown(){
    return new Promise(function(res){
      if (!$('countIn').checked){ res(); return; }
      counting = true; var n = 3, ov = $('count'); ov.hidden = false; ov.textContent = n;
      var h = setInterval(function(){ n--; if (n <= 0){ clearInterval(h); ov.hidden = true; counting = false; res(); } else ov.textContent = n; }, 800);
    });
  }
  function evt(ev, label, vid){
    var o = {ev: ev, t: +((performance.now() - recStartT) / 1000).toFixed(3), wav: +(samples / (ctx ? ctx.sampleRate : 48000)).toFixed(3), vid: +(vid != null ? vid : (pv.currentTime || 0)).toFixed(3)};
    if (label) o.label = label; segs.push(o);
  }
  function mark(){
    if (!pv.duration) return; var m = document.createElement('b'); m.className = 'mk'; m.style.left = (100 * pv.currentTime / pv.duration) + '%'; $('prog').appendChild(m);
  }
  function clearMarks(){ Array.prototype.forEach.call($('prog').querySelectorAll('b.mk'), function(m){ m.remove(); }); }
  function setLive(on){
    $('live').hidden = !on; $('restartBtn').hidden = !on;
    var b = $('recBtn'); b.classList.toggle('stop', on);
    b.innerHTML = on ? '■ Stop recording <kbd>Space</kbd>' : '● Record the whole video <kbd>Space</kbd>';
    $('watchBtn').disabled = on; $('playBtn').disabled = on;
    $('ctl').classList.toggle('on', on && kind === 'full');
    if (!on){ aPaused = false; vPaused = false; vByAudio = false; }
    syncUi();
  }
  function syncUi(){
    var a = $('audBtn'), v = $('vidBtn');
    $('seek').disabled = capturing && !aPaused; $('tp').classList.toggle('scrub', capturing && aPaused); $('pauseHint').hidden = !(capturing && aPaused);
    a.innerHTML = aPaused ? '▶ Resume recording <kbd>A</kbd>' : '⏸ Pause recording <kbd>A</kbd>'; a.classList.toggle('paused', aPaused);
    v.innerHTML = vPaused ? '▶ Resume video <kbd>V</kbd>' : '⏸ Pause video <kbd>V</kbd>'; v.classList.toggle('paused', vPaused);
    var ba = $('bAud'), bv = $('bVid');
    ba.textContent = aPaused ? 'recording paused' : 'mic on'; ba.className = 'badge ' + (aPaused ? 'warn' : 'ok');
    bv.textContent = vPaused ? 'video paused' : 'video playing'; bv.className = 'badge ' + (vPaused ? 'warn' : 'ok');
    $('dot').style.animationPlayState = aPaused ? 'paused' : 'running'; $('dot').style.background = aPaused ? 'var(--warn)' : 'var(--rec)';
  }
  function toggleAudio(){
    if (!capturing || kind !== 'full') return;
    aPaused = !aPaused;
    if (aPaused){                                   // pausing the recording pauses the picture too
      if (!vPaused){ vPaused = true; vByAudio = true; pv.pause(); evt('video_pause'); }
      evt('audio_pause');
    } else {
      evt('audio_resume');
      if (vByAudio){ vPaused = false; vByAudio = false; pv.play().catch(function(){}); evt('video_resume'); }
    }
    syncUi();
    say(aPaused ? '<span class="warn">Paused: the recording and the video are both stopped. Press A to carry on.</span>' : '');
  }
  function toggleVideo(){
    if (!capturing && heldIdx >= 0){ releaseHold(); return; }
    if (!capturing || kind !== 'full') return;
    if (heldIdx >= 0 && vPaused){ releaseHold(); return; }
    vPaused = !vPaused; vByAudio = false;
    if (vPaused){ pv.pause(); evt('video_pause'); } else { pv.play().catch(function(){}); evt('video_resume'); }
    syncUi(); say(vPaused ? '<span class="warn">Picture paused. Your voice is still being recorded.</span>' : '');
  }
  function flag(){
    if (!capturing || kind !== 'full') return;
    evt('flag'); mark(); say('<span class="good">Flagged at ' + mmss(samples / ctx.sampleRate) + '. Keep going, it gets cut out later.</span>');
  }
  function redo(){
    if (!capturing || kind !== 'full') return;
    evt('redo'); mark(); pv.currentTime = Math.max(0, pv.currentTime - 5); evt('video_seek');
    vByAudio = false;
    if (aPaused){ aPaused = false; evt('audio_resume'); }
    if (vPaused){ vPaused = false; pv.play().catch(function(){}); evt('video_resume'); }
    syncUi(); say('<span class="good">Back 5 seconds. Say that bit again.</span>');
  }
  function back5(){
    if (!capturing || kind !== 'full') return;
    pv.currentTime = Math.max(0, pv.currentTime - 5); evt('video_seek');
  }
  function record(k, from){
    if (!stream){ startMic(null).then(function(){ if (stream) record(k, from); }); return; }
    if (ctx.state === 'suspended') ctx.resume();
    if (capturing){ stop(); return; }
    if (counting) return;
    stopPlayback();
    kind = k || 'full'; discard = false;
    if (kind === 'full'){ try{ pv.currentTime = from || 0; }catch(e){} clearMarks(); lastHeld = -1; hideHold(); }
    say('');
    countdown().then(function(){
      chunks = []; savedIdx = 0; samples = 0; recPeak = 0; segs = []; aPaused = false; vPaused = false; capturing = true; recStartT = performance.now(); offsetSec = 0; evt('start');
      setLive(true); if (kind === 'full' && window.vsOnRecordStart) window.vsOnRecordStart();
      try{ if (navigator.wakeLock) navigator.wakeLock.request('screen').then(function(l){ wake = l; }).catch(function(){}); }catch(e){}
      if (kind === 'full'){
        pv.play().then(function(){ offsetSec = (performance.now() - recStartT) / 1000; evt('video_play'); }).catch(function(){});
      }
      var asH = setInterval(function(){ if (!capturing) clearInterval(asH); else autosave(); }, 5000);
      timerH = setInterval(function(){
        var t = samples / (ctx ? ctx.sampleRate : 48000); $('liveTxt').textContent = (kind === 'room' ? 'ROOM TONE ' : (aPaused ? 'PAUSED ' : 'REC ')) + mmss(t);
        if (pv.duration) $('progFill').style.width = Math.min(100, 100 * pv.currentTime / pv.duration) + '%';
      }, 100);
      if (kind === 'room') setTimeout(function(){ if (capturing) stop(); }, 10000);
    });
  }
  /* crash safety: raw audio goes to disk every 5 s, so a crash or closed window loses at most a few seconds */
  function autosave(){
    if (kind !== 'full' || asBusy || savedIdx >= chunks.length) return;
    var part = chunks.slice(savedIdx), upto = chunks.length, n = 0; part.forEach(function(c){ n += c.length; });
    var raw = new Int16Array(n), at = 0; part.forEach(function(c){ raw.set(c, at); at += c.length; });
    asBusy = true;
    xf('/api/autosave?sr=' + ctx.sampleRate + (savedIdx === 0 ? '&reset=1' : ''), {method:'POST', body: raw.buffer}).then(function(){ savedIdx = upto; }).catch(function(){}).then(function(){ asBusy = false; });
  }
  function stop(){
    if (!capturing) return;
    evt('stop'); var segOut = JSON.stringify(segs); capturing = false; clearInterval(timerH); clearTimeout(stopTimer); try{ pv.pause(); }catch(e){}
    try{ if (wake) wake.release(); }catch(e){} wake = null;
    setLive(false); if (window.vsOnRecordStop) window.vsOnRecordStop();
    var w = encodeWav(chunks, ctx.sampleRate); chunks = [];
    if (discard){ say(''); xf('/api/autosave_clear', {method:'POST'}); return; }
    if (kind === 'room'){
      api('/api/roomtone', {method:'POST', body: w.blob}).then(function(){ say('<span class="good">Room tone saved (' + w.sec.toFixed(1) + ' s).</span>'); load(); });
      return;
    }
    var pkdb = w.peak > 0 ? 20 * Math.log10(w.peak) : -90, msg = [];
    if (w.sec < 3){ say('<span class="warn">That was too short, nothing saved.</span>'); xf('/api/autosave_clear', {method:'POST'}); return; }
    msg.push(mmss(w.sec));
    if (pkdb > -1) msg.push('<span class="bad">it clipped: lower the mic gain and record again</span>');
    else if (pkdb < -30) msg.push('<span class="warn">very quiet: move closer to the mic</span>');
    else msg.push('<span class="good">level good (peak ' + pkdb.toFixed(0) + ' dBFS)</span>');
    say('Saving…');
    xf('/api/take?cue=full&sec=' + w.sec.toFixed(3) + '&offset=' + offsetSec.toFixed(3) + '&peak=' + w.peak.toFixed(4) + '&segs=' + encodeURIComponent(segOut), {method:'POST', body: w.blob}).then(function(r){ return r.json(); }).then(function(r){
      xf('/api/autosave_clear', {method:'POST'});
      say('Take ' + r.n + ' saved · ' + msg.join(' · ') + ' · <span class="note">cleaning the voice and writing the script in the background</span>'); return load();
    }).catch(function(){ say('<span class="bad">Could not save the take. Is the studio still running?</span>'); });
  }
  pv.addEventListener('ended', function(){
    if (capturing && kind === 'full'){
      say('The video has finished. Recording carries on until you press Stop. To redo a part, pause and drag the slider back.');
    }
  });
  $('recBtn').onclick = function(){ record('full'); };
  $('redoBtn').onclick = redo; $('flagBtn').onclick = flag; $('backBtn').onclick = back5; $('audBtn').onclick = toggleAudio; $('vidBtn').onclick = toggleVideo;
  window.addEventListener('beforeunload', function(e){ if (capturing){ e.preventDefault(); e.returnValue = ''; } });
  $('restartBtn').onclick = function(){ discard = true; stop(); setTimeout(function(){ record('full'); }, 200); };
  $('roomBtn').onclick = function(){ if (!capturing){ say('<span class="warn">Stay quiet for 10 seconds…</span>'); record('room'); } };
  $('watchBtn').onclick = function(){ stopPlayback(); pv.currentTime = 0; pv.play(); };
  $('folderBtn').onclick = function(){ api('/api/openfolder', {method:'POST'}).then(function(r){ if (r.path) say('<span class="note">Takes folder: ' + esc(r.path) + '</span>'); }); };
  $('exportBtn').onclick = function(){
    api('/api/export', {method:'POST'}).then(function(r){
      say(r.ok ? '<span class="good">Done. I saved your choice (' + esc(r.file) + (r.clean ? ', with the cleaned voice' : '') + '). Your editor or pipeline can pick it up now.</span>' : '<span class="warn">Record a take first.</span>');
    });
  };
  ['countIn'].forEach(function(id){
    var v = store(id); if (v !== null && v !== undefined) $(id).checked = v === '1';
    $(id).onchange = function(){ store(id, $(id).checked ? '1' : '0'); };
  });
  pv.addEventListener('timeupdate', function(){ if (!capturing && pv.duration) $('progFill').style.width = (100 * pv.currentTime / pv.duration) + '%'; });
  document.addEventListener('click', function(e){ var b = e.target.closest && e.target.closest('button'); if (b && !b.closest('dialog')) b.blur(); });
  document.addEventListener('keydown', function(e){
    var tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.ctrlKey || e.metaKey || e.altKey) return;
    if (document.querySelector('dialog[open]')) return;
    if (e.code === 'Space'){ e.preventDefault(); record('full'); }
    else if (e.code === 'KeyA') toggleAudio();
    else if (e.code === 'KeyV') toggleVideo();
    else if (e.code === 'KeyB') back5();
    else if (e.code === 'KeyR') redo();
    else if (e.code === 'KeyM') flag();
    else if (e.code === 'KeyF') addFlagHere();
    else if (e.code === 'KeyN') jumpFlag(1);
    else if (e.code === 'KeyP') jumpFlag(-1);
    else if (e.code === 'KeyH'){ $('holdOn').checked = !$('holdOn').checked; $('holdOn').dispatchEvent(new Event('change')); say('<span class="good">Hold at flags is ' + ($('holdOn').checked ? 'on' : 'off') + '.</span>'); }
    else if (e.code === 'Enter' && heldIdx >= 0) releaseHold();
    else if (e.code === 'KeyK') togglePlay();
    else if (e.code === 'ArrowLeft'){ e.preventDefault(); seekBy(-5); }
    else if (e.code === 'ArrowRight'){ e.preventDefault(); seekBy(5); }
    else if (e.key === '?') $('helpDlg').showModal();
  });
  /* ---------- flags: cue points, 3-2-1 countdown, hold at a flag ---------- */
  var cues = [], heldIdx = -1, lastHeld = -1, lastNum = 0, lastFollow = -2, cuesTimer = null, beepCtx = null;
  function loadCues(){ return api('/api/cues').then(function(r){ cues = (r.cues || []).slice().sort(function(a, b){ return a.t - b.t; }); renderFlags(); renderTicks(); }); }
  function saveCues(){ clearTimeout(cuesTimer); cuesTimer = setTimeout(function(){ xf('/api/cues', {method:'POST', body: JSON.stringify(cues)}); }, 400); }
  function curIdx(t){ var k = -1; for (var i = 0; i < cues.length; i++){ if (cues[i].t <= t + 0.001) k = i; else break; } return k; }
  function renderTicks(){
    var d = pv.duration || 0, box = $('ticks'); if (!d){ box.innerHTML = ''; return; }
    box.innerHTML = cues.map(function(c, i){ return '<i class="' + (c.kind === 'custom' ? 'c' : '') + '" style="left:' + (100 * c.t / d) + '%" title="' + esc(c.label) + ' · ' + mmss(c.t) + '"></i>'; }).join('');
  }
  function renderFlags(){
    var box = $('flagList'); var cur = curIdx(pv.currentTime || 0);
    if (!cues.length){ box.innerHTML = '<div class="note">No flags yet. Press F while the video plays to add one.</div>'; return; }
    box.innerHTML = cues.map(function(c, i){
      return '<div class="fl' + (i === cur ? ' cur' : '') + '" data-i="' + i + '"><span class="fd ' + (c.kind === 'custom' ? 'c' : '') + '"></span><span class="ft" data-j="' + i + '">' + mmss(c.t) + '</span><span class="fn" data-j="' + i + '" title="' + esc(c.label) + '">' + esc(c.label) + '</span>' +
        '<button data-rec="' + i + '" title="Start recording from this flag">● from here</button><button data-rn="' + i + '" title="Rename">✎</button><button data-rm="' + i + '" title="Delete this flag">✕</button></div>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('[data-j]'), function(el){ el.onclick = function(){ jumpTo(cues[+el.dataset.j].t); }; });
    Array.prototype.forEach.call(box.querySelectorAll('[data-rec]'), function(b){ b.onclick = function(){ if (!capturing) record('full', cues[+b.dataset.rec].t); }; });
    Array.prototype.forEach.call(box.querySelectorAll('[data-rn]'), function(b){ b.onclick = function(){ var c = cues[+b.dataset.rn], n = prompt('Name for this flag', c.label); if (n){ c.label = n.slice(0, 80); saveCues(); renderFlags(); renderTicks(); } }; });
    Array.prototype.forEach.call(box.querySelectorAll('[data-rm]'), function(b){ b.onclick = function(){ cues.splice(+b.dataset.rm, 1); saveCues(); renderFlags(); renderTicks(); }; });
  }
  function jumpTo(t){
    var prev = pv.currentTime; pv.currentTime = Math.max(0, t); lastHeld = -1; syncAudio();
    seekLog(prev);
    if (heldIdx >= 0 && !capturing){ hideHold(); }
  }
  function jumpFlag(dir){
    if (!cues.length) return; var t = pv.currentTime || 0, k = curIdx(t), target;
    if (dir > 0) target = cues[k + 1]; else target = (t - (cues[k] ? cues[k].t : 0) > 1.5) ? cues[k] : cues[k - 1];
    if (target) jumpTo(target.t);
  }
  function addFlagHere(){
    var t = pv.currentTime || 0, n = cues.filter(function(c){ return c.kind === 'custom'; }).length + 1;
    cues.push({t: +t.toFixed(2), label: 'Flag ' + n, kind: 'custom'}); cues.sort(function(a, b){ return a.t - b.t; }); saveCues(); renderFlags(); renderTicks();
    if (capturing) evt('flag_add', 'Flag ' + n);
    say('<span class="good">Flag added at ' + mmss(t) + '. Rename it with the pencil in the Flags list.</span>');
  }
  function beep(){
    try{ beepCtx = beepCtx || new (window.AudioContext || window.webkitAudioContext)(); var o = beepCtx.createOscillator(), g = beepCtx.createGain(); o.frequency.value = 880; g.gain.value = 0.08; o.connect(g); g.connect(beepCtx.destination); o.start(); o.stop(beepCtx.currentTime + 0.08); }catch(e){}
  }
  function hideHold(){ heldIdx = -1; $('holdb').hidden = true; }
  function releaseHold(){
    var lbl = heldIdx >= 0 && cues[heldIdx] ? cues[heldIdx].label : '';
    hideHold();
    if (capturing && kind === 'full'){ vPaused = false; vByAudio = false; pv.play().catch(function(){}); evt('video_resume'); evt('release', lbl); syncUi(); say(''); }
    else { pv.play().catch(function(){}); }
  }
  function holdAt(i){
    heldIdx = i; lastHeld = i; pv.pause();
    if (capturing && kind === 'full'){ vPaused = true; vByAudio = false; evt('video_pause'); evt('hold', cues[i].label); syncUi(); }
    $('holdTxt').innerHTML = 'Picture waiting before <b>' + esc(cues[i].label) + '</b>. ' + (capturing ? 'Your voice keeps recording. ' : '') + 'Press V or Enter when you are ready.';
    $('holdb').hidden = false;
  }
  $('holdGo').onclick = releaseHold;
  $('prevFlag').onclick = function(){ jumpFlag(-1); }; $('nextFlag').onclick = function(){ jumpFlag(1); }; $('addFlag').onclick = addFlagHere;
  $('flagReload').onclick = function(){
    if (!confirm('Put the original section flags back? Your own flags will be removed.')) return;
    api('/api/cues_reset', {method:'POST'}).then(function(){ return loadCues(); }).catch(function(){ say('<span class="bad">Could not reset the flags.</span>'); });
  };
  ['holdOn', 'beepOn', 'followVid'].forEach(function(id){
    var v = store(id); if (v !== null && v !== undefined) $(id).checked = v === '1';
    $(id).onchange = function(){ store(id, $(id).checked ? '1' : '0'); };
  });
  pv.addEventListener('loadedmetadata', renderTicks);
  pv.addEventListener('seeked', function(){ var k = lastHeld >= 0 && cues[lastHeld] ? cues[lastHeld].t : null; if (k !== null && pv.currentTime < k - 0.6) lastHeld = -1; });
  /* script follows the picture: scroll to the [m:ss] block that is on screen */
  var mirror = null;
  function lineOffset(upTo){
    if (!mirror){ mirror = document.createElement('div'); mirror.style.cssText = 'position:absolute;visibility:hidden;white-space:pre-wrap;word-wrap:break-word;left:-9999px;top:0'; document.body.appendChild(mirror); }
    var cs = getComputedStyle(txt); mirror.style.width = txt.clientWidth + 'px'; mirror.style.font = cs.font; mirror.style.lineHeight = cs.lineHeight; mirror.style.padding = cs.padding;
    mirror.textContent = upTo; return mirror.scrollHeight;
  }
  function followScript(t){
    if (panel.hidden || !$('followVid').checked || document.activeElement === txt) return;
    var lines = txt.value.split('\n'), best = -1, bestT = -1;
    for (var i = 0; i < lines.length; i++){ var m = /^\[(\d+):(\d+(?:\.\d+)?)\]/.exec(lines[i]); if (m){ var tt = parseInt(m[1]) * 60 + parseFloat(m[2]); if (tt <= t + 0.05){ best = i; bestT = tt; } } }
    if (best === lastFollow) return; lastFollow = best; if (best < 0) return;
    var off = lineOffset(lines.slice(0, best).join('\n') + (best ? '\n' : ''));
    try{ txt.scrollTo({top: Math.max(0, off - 14), behavior: 'smooth'}); }catch(e){ txt.scrollTop = Math.max(0, off - 14); }
  }
  /* the clock that drives the countdown, the hold and the script */
  var lastCur = -2;
  setInterval(function(){
    var t = pv.currentTime || 0, playing = !pv.paused && !pv.ended, active = playing || capturing || heldIdx >= 0;
    var k = curIdx(t), nx = cues[k + 1];
    var np = $('nowp'); if (cues.length && k >= 0 && (active || !pv.paused)){ np.hidden = false; np.textContent = 'Now: ' + cues[k].label; } else np.hidden = true;
    if (k !== lastCur){ lastCur = k; renderFlags(); renderTicks(); Array.prototype.forEach.call($('ticks').children, function(el, i){ el.classList.toggle('cur', i === k); }); }
    followScript(t);
    if (!(capturing && kind === 'room')){
      var rem = nx ? nx.t - t : 99;
      if (playing && nx && rem > 0.03 && rem <= 3.0){
        var num = Math.min(3, Math.max(1, Math.ceil(rem))); var nb = $('nextb'); nb.hidden = false; $('nextLbl').textContent = nx.label;
        if (num !== lastNum){ lastNum = num; var b = $('nextNum'); b.textContent = num; b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop'); if ($('beepOn').checked) beep(); }
      } else if (heldIdx < 0){ $('nextb').hidden = true; lastNum = 0; }
      if ($('holdOn').checked && playing && nx && rem <= 0.14 && rem > -0.6 && lastHeld !== k + 1 && heldIdx < 0 && !counting){ $('nextb').hidden = true; holdAt(k + 1); }
    }
  }, 40);
  loadCues();
  /* ---------- change video ---------- */
  function showVideoInfo(v){ if (v) $('vidInfo').textContent = 'Video: ' + (v.name || 'proxy.mp4') + (v.duration ? ' · ' + mmss(v.duration) : ''); }
  $('vidChangeBtn').onclick = function(){ if (capturing) return; if (!info.capabilities.ffmpeg){ say('<span class="warn">Install ffmpeg to change the video.</span>'); return; } $('vidFile').click(); };
  $('vidFile').onchange = function(){
    var f = $('vidFile').files[0]; if (!f) return;
    var ok = confirm('Use "' + f.name + '" as the video to record against?\n\nYour saved takes stay, but they were recorded against the old video, so record again for the new one.');
    if (!ok){ $('vidFile').value = ''; return; }
    stopPlayback(); $('vidChangeBtn').disabled = true; say('<span class="warn"><span class="spin"></span>Sending the video to the studio… (' + Math.round(f.size / 1048576) + ' MB)</span>');
    xf('/api/setvideo?name=' + encodeURIComponent(f.name), {method:'POST', body: f}).then(function(r){ return r.json(); }).then(function(r){
      if (r.error){ say('<span class="bad">' + esc(r.error) + '</span>'); $('vidChangeBtn').disabled = false; return; }
      say('<span class="warn"><span class="spin"></span>Preparing the video… this takes about a minute.</span>');
      (function poll(){ api('/api/video_status').then(function(j){
        if (j.state === 'working') return setTimeout(poll, 2000);
        $('vidChangeBtn').disabled = false; $('vidFile').value = '';
        if (j.state === 'error'){ say('<span class="bad">' + esc(j.msg) + '</span>'); return; }
        pv.src = '/proxy.mp4?t=' + Date.now(); pv.load(); showVideoInfo(j.video); clearMarks(); $('progFill').style.width = '0%';
        say('<span class="good">New video ready. Press Record when you are ready.</span>');
      }); })();
    }).catch(function(){ say('<span class="bad">Could not send the video. Is the studio still running?</span>'); $('vidChangeBtn').disabled = false; });
  };
  /* ---------- projects (sets) and about ---------- */
  function loadInfo(){
    return api('/api/info').then(function(r){
      info = r; var sel = $('setSel');
      $('aboutTxt').textContent = 'Voice Studio ' + r.version + ' · data folder: ' + r.data_dir;
      $('brandSub').textContent = r.current_set ? 'project: ' + r.current_set : 'voice-over recorder';
      document.title = 'Voice Studio' + (r.current_set ? ' · ' + r.current_set : '');
      $('setPick').hidden = !r.sets.length;
      sel.innerHTML = (r.current_set && r.sets.indexOf(r.current_set) < 0 ? '<option value="">' + esc(r.current_set) + '</option>' : '') +
        r.sets.map(function(n){ return '<option value="' + esc(n) + '">' + esc(n) + '</option>'; }).join('');
      sel.value = r.current_set || '';
      renderTakes();
    }).catch(function(){});
  }
  $('setSel').onchange = function(){
    var name = $('setSel').value;
    if (capturing || !name || name === info.current_set){ $('setSel').value = info.current_set || ''; return; }
    if (!confirm('Switch to the project "' + name + '"?\n\nThe takes of "' + (info.current_set || 'this project') + '" move to recordings_archive and stay safe there.')){ $('setSel').value = info.current_set || ''; return; }
    stopPlayback(); player.removeAttribute('src'); player.load();
    api('/api/sets/switch?name=' + encodeURIComponent(name), {method:'POST'}).then(function(r){
      if (r.error){ say('<span class="bad">' + esc(r.error) + '</span>'); $('setSel').value = info.current_set || ''; return; }
      waveCache = {}; pv.src = '/proxy.mp4?t=' + Date.now(); pv.load(); clearMarks(); $('progFill').style.width = '0%';
      say('<span class="good">Project "' + esc(name) + '" loaded. The takes of "' + esc(r.previous) + '" are in recordings_archive.</span>');
      return Promise.all([loadInfo(), loadCues(), load().then(function(){ showVideoInfo(state.video); })]);
    }).catch(function(){ say('<span class="bad">Could not reach the studio. Is it still running?</span>'); });
  };
  loadInfo();
  /* reuse the microphone permission if it was already granted */
  try{ navigator.permissions.query({name:'microphone'}).then(function(p){ if (p.state === 'granted') startMic(null); }); }catch(e){}
  load().then(function(){
    showVideoInfo(state.video);
    if (state.autosave > 3 && !capturing){
      say('<span class="warn">An unsaved recording of ' + mmss(state.autosave) + ' was found (the studio closed while recording).</span> <button id="recoverBtn" class="primary">Recover it as a take</button> <button id="dropBtn">Discard</button>');
      $('recoverBtn').onclick = function(){ api('/api/autosave_recover', {method:'POST'}).then(function(r){ say(r.n ? '<span class="good">Recovered as Take ' + r.n + '.</span>' : '<span class="bad">Could not recover it.</span>'); load(); }); };
      $('dropBtn').onclick = function(){ api('/api/autosave_clear', {method:'POST'}).then(function(){ say(''); load(); }); };
    }
  });
})();
