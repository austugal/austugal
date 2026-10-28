"""Development-server launcher compatible with JS preview runners."""
import sys
from control.server import main

if __name__ == '__main__':
    sys.argv = [arg for arg in sys.argv if arg != '--strictPort']
    main()
