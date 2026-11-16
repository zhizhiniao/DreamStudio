"""Run yt-dlp with a private cookie copy, preserving imported login credentials."""
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def isolated_cookie_args(args):
    args = list(args)
    with tempfile.TemporaryDirectory(prefix='y2a-youtube-') as directory:
        for index, argument in enumerate(args):
            if argument == '--cookies' and index + 1 < len(args):
                source = Path(args[index + 1])
                if source.is_file():
                    copy = Path(directory) / f'cookies-{index}.txt'
                    copy.write_bytes(source.read_bytes())
                    os.chmod(copy, 0o600)
                    args[index + 1] = str(copy)
        yield args


def main(args=None):
    import yt_dlp
    with isolated_cookie_args(sys.argv[1:] if args is None else args) as isolated:
        return yt_dlp.main(isolated)


if __name__ == '__main__':
    main()
