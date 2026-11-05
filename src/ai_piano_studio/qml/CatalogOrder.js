.pragma library

function ordered(items, mode, labelKey) {
    // Tie by the original position: Qt's JavaScript sort is not always stable.
    var entries = items.map(function(item, index) { return {item: item, index: index}; });
    entries.sort(function(a, b) {
        var difference;
        if (mode === "name") {
            difference = String(a.item[labelKey] || a.item.id || "").localeCompare(
                String(b.item[labelKey] || b.item.id || ""));
        } else {
            var first = typeof a.item.recommendationPriority === "number" ? a.item.recommendationPriority : 1000000000;
            var second = typeof b.item.recommendationPriority === "number" ? b.item.recommendationPriority : 1000000000;
            difference = first - second;
        }
        return difference || a.index - b.index;
    });
    return entries.map(function(entry) { return entry.item; });
}
