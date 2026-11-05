import QtQuick
import QtQuick.Controls

Rectangle {
    id: lane
    required property var theme
    required property var timeline
    objectName: "pedalEventLane"
    height: 48
    color: theme.popupBackground
    border.color: theme.borderStrong
    clip: true
    property var events: visible ? JSON.parse(editor.pedalEventsJson) : []
    property string draggingId: ""
    property real draftTime: 0
    property int draftValue: 0
    readonly property var displayedEvents: events.map(e => e.id === draggingId
        ? Object.assign({}, e, {time:draftTime, value:draftValue}) : e)
    function cancelDrag() { draggingId="" }
    Keys.onEscapePressed: event => { cancelDrag(); event.accepted=true }
    onActiveFocusChanged: if(!activeFocus) cancelDrag()
    onVisibleChanged: { if(!visible) cancelDrag(); else curve.requestPaint() }
    Label { x: 8; anchors.verticalCenter:parent.verticalCenter; text:"踏板"; color:lane.theme.textSecondary; font.pixelSize:11 }
    Canvas {
        id: curve
        x: lane.timeline.keyWidth; width:Math.max(0,lane.width-x); height:parent.height
        onPaint: {
            if(!lane.visible)return
            let ctx=getContext("2d");ctx.reset();ctx.strokeStyle=lane.theme.textPrimary;ctx.lineWidth=1.5
            let points=lane.displayedEvents.slice().sort((a,b)=>a.time-b.time || a.order-b.order)
            let value=0,start=lane.timeline.viewStartSeconds,scale=lane.timeline.pixelsPerSecond
            for (let p of points) { if(p.time<=start) value=p.value }
            ctx.beginPath();ctx.moveTo(0,40-value/127*30)
            for (let p of points) {
                if(p.time<start) continue
                let px=(p.time-start)*scale
                ctx.lineTo(px,40-value/127*30);value=p.value;ctx.lineTo(px,40-value/127*30)
            }
            ctx.lineTo(width,40-value/127*30);ctx.stroke()
        }
        MouseArea {
            anchors.fill:parent; enabled:!keyboard.recording
            onDoubleClicked: mouse => editor.addPedalEvent(lane.timeline.viewStartSeconds+mouse.x/lane.timeline.pixelsPerSecond,mouse.y<height/2?127:0)
        }
    }
    Repeater {
        model: lane.visible ? lane.events : []
        Rectangle {
            id: point
            objectName: "pedalPoint-"+modelData.id
            required property var modelData
            x: lane.timeline.keyWidth+((lane.draggingId===modelData.id?lane.draftTime:modelData.time)-lane.timeline.viewStartSeconds)*lane.timeline.pixelsPerSecond-6
            y: 40-(lane.draggingId===modelData.id?lane.draftValue:modelData.value)/127*30-6; width:12;height:12;radius:6
            visible:x>=lane.timeline.keyWidth-6 && x<lane.width
            color:lane.theme.textPrimary
            MouseArea {
                anchors.fill:parent; anchors.margins:-4; enabled:!keyboard.recording
                acceptedButtons:Qt.LeftButton|Qt.RightButton
                onPressed:mouse=>{
                    lane.draftTime=point.modelData.time;lane.draftValue=point.modelData.value
                    lane.draggingId=point.modelData.id;lane.forceActiveFocus()
                }
                onPositionChanged:mouse=>{
                    if(pressed && pressedButtons===Qt.LeftButton && lane.draggingId===point.modelData.id){
                        let pos=mapToItem(lane,mouse.x,mouse.y)
                        lane.draftTime=Math.max(0,lane.timeline.viewStartSeconds+(pos.x-lane.timeline.keyWidth)/lane.timeline.pixelsPerSecond)
                        lane.draftValue=Math.max(0,Math.min(127,Math.round((40-pos.y)/30*127)))
                    }
                }
                onReleased:mouse=>{
                    if(lane.draggingId!==point.modelData.id)return
                    let id=point.modelData.id,time=lane.draftTime,value=lane.draftValue
                    lane.cancelDrag()
                    if(mouse.button===Qt.RightButton)editor.deletePedalEvent(id)
                    else editor.updatePedalEvent(id,time,value)
                }
                onCanceled:lane.cancelDrag()
                ToolTip.visible:containsMouse; hoverEnabled:true
                ToolTip.text:point.modelData.time.toFixed(3)+" s · "+point.modelData.value+"\n拖动调整；右键删除"
            }
        }
    }
    onEventsChanged:curve.requestPaint()
    onDisplayedEventsChanged:curve.requestPaint()
    onWidthChanged:curve.requestPaint()
    Connections {
        target:lane.timeline
        function onViewStartSecondsChanged(){curve.requestPaint()}
        function onPixelsPerSecondChanged(){curve.requestPaint()}
    }
}
