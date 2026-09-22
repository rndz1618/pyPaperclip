import os
import uvicorn


def main() -> None:
    uvicorn.run(
        "pypaperclip.app:app",
        host=os.getenv("PYPAPERCLIP_HOST", "127.0.0.1"),
        port=int(os.getenv("PYPAPERCLIP_PORT", "8000")),
        reload=os.getenv("PYPAPERCLIP_RELOAD", "0") == "1",
    )


if __name__ == "__main__":
    main()
