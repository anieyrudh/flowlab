from server.flowlab.structured_pipe_reference import open_boundary_ogrid_dict, reference_spec


def test_structured_reference_uses_open_ends_and_hex_only_ogrid() -> None:
    spec = reference_spec()
    text = open_boundary_ogrid_dict(spec, 0.000625)

    assert text.count("hex (") == 5
    assert "type cyclic" not in text
    assert text.count("type patch;") == 2
    assert "neighbourPatch" not in text
    assert "type wall" in text
    assert "arc " in text
