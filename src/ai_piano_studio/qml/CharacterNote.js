.pragma library

// Port of qingyv-promo-video/production/CharacterNote.qml. One drawing routine
// serves both ordinary delegates and the bounded dense-project canvas.
function paint(c, x, y, w, h, character, age, duration, velocity) {
    if (w < 2 || h < 2 || character === "none") return;
    var angular = character === "hack";
    var gale = character === "gale";
    var traveler = character === "traveler";
    var bee = character === "bee";
    var release = age - duration;
    var live = age >= 0 && release <= 0;
    var energy = (live ? 1 : (release > 0 ? Math.max(0, 1 - release / (angular ? .13 : .36)) : 0))
        * (.4 + .6 * Math.max(0, Math.min(127, velocity)) / 127);
    c.save();
    var alpha = c.globalAlpha;
    c.translate(x, y);
    c.beginPath();
    if (angular) {
        var cut = Math.min(5, w * .2, h * .4);
        c.moveTo(cut, 0); c.lineTo(w, 0); c.lineTo(w, h - cut);
        c.lineTo(w - cut, h); c.lineTo(0, h); c.lineTo(0, cut);
    } else {
        var r = Math.min(h / 2, w / 2);
        c.moveTo(r, 0); c.lineTo(w - r, 0); c.quadraticCurveTo(w, 0, w, r);
        c.lineTo(w, h - r); c.quadraticCurveTo(w, h, w - r, h);
        c.lineTo(r, h); c.quadraticCurveTo(0, h, 0, h - r);
        c.lineTo(0, r); c.quadraticCurveTo(0, 0, r, 0);
    }
    c.closePath(); c.clip();
    var g = c.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, gale ? '#ffe6be' : traveler ? '#e8f1ef' : bee ? '#e5efd4' : angular ? '#e2d3fa' : '#f6e5bd');
    g.addColorStop(.45, gale ? '#f5ad91' : traveler ? '#9ebfcf' : bee ? '#a4c8ae' : angular ? '#a69abd' : '#d5b680');
    g.addColorStop(1, gale ? '#ac624e' : traveler ? '#52778c' : bee ? '#597e71' : angular ? '#575167' : '#90704a');
    c.globalAlpha = alpha * (.76 + energy * .24);
    c.fillStyle = g; c.fillRect(0, 0, w, h);
    if (!angular) {
        var at = live ? Math.min(1, age / Math.max(.12, duration)) : .65;
        var shine = c.createLinearGradient(at * w - 24, 0, at * w + 24, 0);
        shine.addColorStop(0, 'rgba(255,244,213,0)');
        shine.addColorStop(.5, 'rgba(255,244,213,' + (.12 + energy * .65) + ')');
        shine.addColorStop(1, 'rgba(255,244,213,0)');
        c.fillStyle = shine; c.fillRect(0, 0, w, h);
        c.strokeStyle = 'rgba(255,240,204,' + (.25 + energy * .45) + ')';
        c.lineWidth = 1; c.beginPath(); c.moveTo(5, 2);
        c.quadraticCurveTo(w * .5, 0, w - 5, 2); c.stroke();
    } else {
        c.globalAlpha = alpha * (.18 + energy * .24); c.fillStyle = '#f0e8ff';
        c.beginPath(); c.moveTo(w * .42, 0); c.lineTo(w * .63, h);
        c.lineTo(w * .85, h); c.lineTo(w * .64, 0); c.closePath(); c.fill();
        c.globalAlpha = alpha;
        var attack = live ? Math.max(0, 1 - age / .13) : 0;
        c.strokeStyle = 'rgba(246,239,255,' + (.32 + energy * .28 + attack * .35) + ')';
        c.lineWidth = 1; c.beginPath(); c.moveTo(5, .5); c.lineTo(w, .5);
        c.moveTo(w - .5, 1); c.lineTo(w - .5, h - 5); c.stroke();
        c.fillStyle = 'rgba(249,243,255,' + (energy * .65) + ')';
        c.fillRect(0, 1, Math.min(3, w), h - 2);
    }
    if (gale) {
        var kick = age >= 0 ? Math.max(0, 1 - age / .18) : 0;
        c.globalAlpha = alpha * (.18 + kick * .62); c.fillStyle = '#fff3c9'; c.beginPath();
        var px = Math.max(2, Math.min(w - 4, 4 + Math.max(0, age) * 45));
        c.moveTo(px, 1); c.quadraticCurveTo(px + 8, h * .2, px + 3, h - 1);
        c.quadraticCurveTo(px - 5, h * .65, px, 1); c.fill();
    }
    if (traveler) {
        c.globalAlpha = alpha;
        c.strokeStyle = 'rgba(242,224,174,' + (.15 + energy * .55) + ')';
        c.lineWidth = 1; c.beginPath(); c.moveTo(3, h * .65);
        c.quadraticCurveTo(w * .5, h * .25, w - 3, h * .65); c.stroke();
    }
    c.restore();
}
