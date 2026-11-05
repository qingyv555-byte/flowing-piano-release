import QtQuick
import "AIBrandArtwork.js" as BrandArtwork

Item {
    id: root
    required property Theme theme
    property string providerId: ""
    property string modelId: ""
    implicitWidth: 24
    implicitHeight: 24
    readonly property string brand: {
        var name = modelId.toLowerCase()
        if (/deepseek/.test(name)) return "deepseek"
        if (/claude/.test(name)) return "claude"
        if (/gemini|gemma/.test(name)) return "gemini"
        if (/qwen|qwq/.test(name)) return "qwen"
        if (/glm-/.test(name)) return "zai"
        if (/doubao|seed-|ark-code/.test(name)) return "doubao"
        if (/minimax/.test(name)) return "minimax"
        if (/kimi|moonshot/.test(name)) return "moonshot"
        if (/(^|\/)(gpt-|o[134]-|o[134]$|chatgpt)/.test(name)) return "openai"
        var brands = {openai: "openai", codex: "openai", deepseek: "deepseek",
            anthropic: "anthropic", gemini: "gemini", qwen: "qwen",
            kimi: "moonshot", ollama: "ollama", glm: "zai", doubao: "doubao",
            minimax: "minimax", volcengine: "volcengine", openrouter: "openrouter"}
        return brands[providerId] || ""
    }
    Rectangle {
        anchors.fill: parent
        radius: Math.min(8, width / 4)
        color: root.theme.inputBackground
        border.color: root.theme.borderSubtle
    }
    Image {
        id: logo
        objectName: "aiBrandImage"
        anchors.fill: parent
        anchors.margins: root.width >= 30 ? 4 : 2
        source: BrandArtwork.sourceFor(root.brand, root.theme.textPrimary.toString())
        sourceSize.width: Math.ceil(root.width * 2)
        sourceSize.height: Math.ceil(root.height * 2)
        fillMode: Image.PreserveAspectFit
    }
}
