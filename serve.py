from __future__ import annotations

from samv.http.server import main

if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
