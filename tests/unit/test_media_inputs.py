import pytest
from PySide6.QtGui import QImage
from ai_piano_studio.creative.media import prepare_image


def test_image_resizes_and_strips_text_metadata(tmp_path):
    source = QImage(3000, 1000, QImage.Format_RGB32)
    source.fill(0xff6699aa)
    source.setText('secret', 'private metadata')
    path = tmp_path / 'source.png'
    assert source.save(str(path))
    image = prepare_image(path)
    assert image.width == 2048
    assert abs(image.height - 2048 / 3) < 1  # Qt rounds the scaled height down.
    decoded = QImage.fromData(image.data)
    assert not decoded.text('secret')
    assert QImage(str(path)).text('secret') == 'private metadata'


def test_bad_oversize_or_remote_image_is_rejected(tmp_path):
    path = tmp_path / 'bad.png'
    path.write_bytes(b'not an image')
    with pytest.raises(ValueError):
        prepare_image(path)
    with pytest.raises(ValueError):
        prepare_image('https://example.test/private.png')
    path.write_bytes(b'x' * (10 * 1024 * 1024 + 1))
    with pytest.raises(ValueError):
        prepare_image(path)


def test_image_attachment_stays_on_user_turn_without_mutating_tool_continuation(tmp_path):
    from ai_piano_studio.creative.media import attach_openai_image
    source = QImage(2, 2, QImage.Format_RGB32)
    source.fill(0xff6699aa)
    path = tmp_path / 'image.png'
    source.save(str(path))
    image = prepare_image(path)
    messages = [{'role': 'user', 'content': 'scene'},
                {'role': 'assistant', 'content': '', 'reasoning_content': 'private-signature'},
                {'role': 'tool', 'tool_call_id': 'one', 'content': 'ok'}]
    result = attach_openai_image(messages, [image])
    assert result[0]['content'][1]['image_url']['url'] == image.data_url
    assert result[1:] == messages[1:]
    assert messages[0]['content'] == 'scene'
