.pragma library

function describe(value) {
    var position = Math.max(0, Math.min(1, Number(value)))
    var character = position < 0.2 ? "柔和" : position < 0.4 ? "温暖"
        : position <= 0.6 ? "均衡" : position <= 0.8 ? "清亮" : "明亮"
    return character + " · " + Math.round(position * 100) + "%"
}
