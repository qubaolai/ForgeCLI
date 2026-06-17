from forgecli.interfaces.cli.app import main


def test_main_print(capsys) -> None:
    main()

    captured = capsys.readouterr()

    assert captured.out == "forgecli\n"
    assert captured.err == ""
