import sys

if len(sys.argv) > 1 and sys.argv[1] not in ("--no-browser",):
    from .cli import main
    main()
else:
    from .server import main
    main(open_browser="--no-browser" not in sys.argv)
