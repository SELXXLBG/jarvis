import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
import traceback

print("--- DEBUG STARTER ---")
try:
    print("Attempting to import main.py...")
    import main
    print("Import successful. Starting main()...")
    main.main()
except Exception as e:
    print("\n🔴 IMPORT OR EXECUTION ERROR DETECTED:")
    traceback.print_exc()
    print("\n" + "="*50)
    input("Press ENTER to close...")
