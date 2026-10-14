(function () {
  'use strict';
  var SHIPS = [['Frigate',5],['Brig',4],['Schooner',3],['Sloop',3],['Cutter',2]];
  var STORE = 'beacons-connect-revolution-sea-practice-v1';
  var gridMine = document.getElementById('practice-mine');
  var gridEnemy = document.getElementById('practice-enemy');
  var panels = document.getElementById('practice-panels');
  var status = document.getElementById('practice-status');
  var fire = document.getElementById('practice-fire');
  var aim = document.getElementById('practice-aim');
  var selected = null;
  var model;
  var letter = function (i) {return 'ABCDEFGHIJ'[i % 10] + (Math.floor(i / 10) + 1);};
  var allCells = function (ships) {return new Set(ships.flatMap(function(s) {return s.cells;}));};
  var takenShots = function (shots) {return new Set(shots);};
  var isSunk = function (ship, shots) {return ship.cells.every(function(c) {return shots.includes(c);});};
  var allSunk = function (ships, shots) {return ships.every(function(s) {return isSunk(s,shots);});};
  var choice = function (arr) {return arr[Math.floor(Math.random()*arr.length)];};
  function randomFleet() {
    var placed = [], used = new Set();
    SHIPS.forEach(function (entry) {
      var name = entry[0], len = entry[1], cells;
      do {
        var h = Math.random() < .5;
        var r = Math.floor(Math.random()*(h ? 10 : 11-len));
        var c = Math.floor(Math.random()*(h ? 11-len : 10));
        cells = Array.from({length:len},function(_,i) {return (r+(h?0:i))*10 + c+(h?i:0);});
      } while(cells.some(function(x) {return used.has(x);}));
      cells.forEach(function(x) {used.add(x);});
      placed.push({name:name,cells:cells});
    });
    return placed;
  }
  function fresh() {return {phase:'placing',mine:randomFleet(),theirs:randomFleet(),myShots:[],aiShots:[],winner:null,log:[],computerBusy:false};}
  function stored() {
    try {
      var candidate = JSON.parse(localStorage.getItem(STORE));
      if (candidate && Array.isArray(candidate.mine) && candidate.mine.length===5 && Array.isArray(candidate.theirs) && candidate.theirs.length===5 && Array.isArray(candidate.myShots) && Array.isArray(candidate.aiShots) && ['placing','firing','finished'].includes(candidate.phase)) return candidate;
    } catch (err) { /* localStorage denied or invalid: use fresh state */ }
    return fresh();
  }
  function save() {try {localStorage.setItem(STORE,JSON.stringify(model));} catch(err) {/* no storage available */}}
  function log(line) {model.log.unshift(line);model.log=model.log.slice(0,20);}
  function axis(cols, rows) {
    document.getElementById(cols).innerHTML='ABCDEFGHIJ'.split('').map(function(l){return '<span>'+l+'</span>';}).join('');
    document.getElementById(rows).innerHTML=Array.from({length:10},function(_,i){return '<span>'+(i+1)+'</span>';}).join('');
  }
  axis('my-cols','my-rows');axis('their-cols','their-rows');
  function renderGrid(el, mySide) {
    var shots = takenShots(mySide ? model.aiShots : model.myShots);
    var shipSet = allCells(mySide ? model.mine : model.theirs);
    var canSelect = !mySide && model.phase === 'firing' && !model.computerBusy;
    el.innerHTML = '';
    for(var i=0;i<100;i++){
      var cell=document.createElement(mySide?'span':'button');
      var hit = shots.has(i) && shipSet.has(i);
      var miss = shots.has(i) && !shipSet.has(i);
      cell.className='naval-cell'+(mySide && shipSet.has(i)?' occupied':'')+(hit?' hit':'')+(miss?' miss':'')+(!mySide && selected===i?' aimed':'');
      if(mySide) cell.setAttribute('role','presentation');
      else {cell.type='button';cell.disabled=!canSelect||shots.has(i);cell.dataset.cell=String(i);cell.setAttribute('aria-pressed',selected===i?'true':'false');}
      cell.setAttribute('aria-label',letter(i)+(hit?' hit':miss?' miss':mySide&&shipSet.has(i)?' ship':' water'));
      if(hit || miss){var mark=document.createElement('span');mark.className='naval-mark'+(miss?' naval-splash':'');mark.textContent=hit?'✕':'✳';cell.appendChild(mark);}
      if(!mySide) cell.addEventListener('click',(function(n){return function(){selectTarget(n);};})(i));
      el.appendChild(cell);
    }
    var revealedShips = mySide?model.mine:model.theirs.filter(function(s){return isSunk(s,model.myShots);});
    BeaconNaval.drawShips(el,revealedShips);
  }
  function render() {
    panels.className='naval-panels '+(model.phase==='placing'?'placing':'firing');
    renderGrid(gridMine,true);renderGrid(gridEnemy,false);
    document.getElementById('practice-shuffle').disabled=model.phase!=='placing';
    document.getElementById('practice-ready').disabled=model.phase!=='placing';
    document.getElementById('practice-manifest').innerHTML=model.mine.map(function(s) {return '<span class="naval-ship-tag'+(isSunk(s,model.aiShots)?' sunk':'')+'">'+s.name+' <small>'+s.cells.length+' squares</small>'+(isSunk(s,model.aiShots)?' · sunk':'')+'</span>';}).join('');
    var enemySunk=model.theirs.filter(function(s){return isSunk(s,model.myShots);}).map(function(s){return s.name;});
    document.getElementById('practice-enemy-sunk').textContent=enemySunk.length?'Enemy vessels sunk: '+enemySunk.join(', '):'No Royal Navy vessels sunk yet.';
    fire.disabled=model.phase!=='firing'||selected===null||model.computerBusy;
    aim.textContent=model.phase==='placing'?'Prepare your fleet first':model.phase==='finished'?'Battle concluded':model.computerBusy?'The Royal Navy is firing…':selected===null?'Aim at a square':'Target: '+letter(selected);
    document.getElementById('practice-history').replaceChildren();
    model.log.forEach(function(line){var el=document.createElement('li');el.textContent=line;document.getElementById('practice-history').appendChild(el);});
    if(model.phase==='placing') status.textContent='Prepare for battle: shuffle or ready your fleet.';
    else if(model.phase==='finished') status.textContent=model.winner==='you'?'Victory! You defeated the Royal Navy.':'Defeat. The Royal Navy sank your fleet.';
    else if(model.computerBusy) status.textContent='Royal Navy turn — incoming cannon fire!';
    else status.textContent='Your turn — select a coordinate and FIRE!';
    save();
  }
  function selectTarget(n) {if(model.phase!=='firing'||model.computerBusy||model.myShots.includes(n))return;selected=n;render();}
  function neighbor(n) {var r=Math.floor(n/10),c=n%10;return [[r-1,c],[r+1,c],[r,c-1],[r,c+1]].filter(function(p){return p[0]>=0&&p[0]<10&&p[1]>=0&&p[1]<10;}).map(function(p){return p[0]*10+p[1];});}
  function aiPick() {
    var taken=takenShots(model.aiShots), own=allCells(model.mine);
    var hits=model.aiShots.filter(function(n) {return own.has(n) && !model.mine.some(function(s){return s.cells.includes(n)&&isSunk(s,model.aiShots);});});
    var adjacent=Array.from(new Set(hits.flatMap(neighbor))).filter(function(n){return !taken.has(n);});
    if(adjacent.length) return choice(adjacent);
    var unseen=Array.from({length:100},function(_,i){return i;}).filter(function(i){return !taken.has(i);});
    var checker=unseen.filter(function(i){return (Math.floor(i/10)+i%10)%2===0;});
    return choice(checker.length?checker:unseen);
  }
  function computerTurn() {
    if(model.phase!=='firing')return;
    var n=aiPick();
    model.aiShots.push(n);
    var hit=allCells(model.mine).has(n);
    var sunken=model.mine.find(function(s){return s.cells.includes(n)&&isSunk(s,model.aiShots);});
    log('Royal Navy fires '+letter(n)+' — '+(hit?'hit':'miss')+(sunken?'! Your '+sunken.name+' sank.':'.'));
    model.computerBusy=false;
    if(allSunk(model.mine,model.aiShots)) {model.winner='computer';model.phase='finished';log('The Royal Navy wins.');}
    render();
  }
  document.getElementById('practice-shuffle').addEventListener('click',function(){if(model.phase!=='placing')return;model.mine=randomFleet();render();});
  document.getElementById('practice-ready').addEventListener('click',function(){if(model.phase!=='placing')return;model.phase='firing';log('Fleet deployed. The first cannon shot is yours.');render();});
  fire.addEventListener('click',function(){
    if(model.phase!=='firing'||model.computerBusy||selected===null||model.myShots.includes(selected))return;
    var n=selected;selected=null;model.myShots.push(n);
    var hit=allCells(model.theirs).has(n);
    var sunken=model.theirs.find(function(s){return s.cells.includes(n)&&isSunk(s,model.myShots);});
    log('Continental Navy fires '+letter(n)+' — '+(hit?'hit':'miss')+(sunken?'! Enemy '+sunken.name+' sank.':'.'));
    if(allSunk(model.theirs,model.myShots)) {model.phase='finished';model.winner='you';log('The Royal Navy has been defeated!');render();return;}
    model.computerBusy=true;render();setTimeout(computerTurn,550);
  });
  document.getElementById('practice-new').addEventListener('click',function(){model=fresh();selected=null;render();});
  var themeButton=document.getElementById('practice-theme');
  function theme(t){document.body.classList.toggle('theme-dark',t==='dark');themeButton.textContent=t==='dark'?'Light mode':'Dark mode';}
  try {theme(localStorage.getItem('beacons-theme')||localStorage.getItem('beacons-home-theme')||'light');}catch(err){theme('light');}
  themeButton.addEventListener('click',function(){var t=document.body.classList.contains('theme-dark')?'light':'dark';theme(t);try{localStorage.setItem('beacons-theme',t);}catch(err){}});
  model=stored();
  // A paused computer turn resumes after reloading the page.
  if(model.computerBusy && model.phase==='firing') setTimeout(computerTurn,550);
  render();
  window.addEventListener('resize',function(){BeaconNaval.drawShips(gridMine,model.mine);BeaconNaval.drawShips(gridEnemy,model.theirs.filter(function(s){return isSunk(s,model.myShots);}));});
})();
