"""Refresh ONLY the built-in fake offers in an existing local demo DB.
Usage: python refresh_demo_offers.py. Creates a timestamped backup first.
"""
import os, sqlite3, shutil
from pathlib import Path
from datetime import datetime, timedelta, timezone
ROOT=Path(__file__).parent
if os.getenv('DEMO_MODE','1')!='1':
    raise SystemExit('ERROR: demo refresh is disabled when DEMO_MODE != 1')
db=Path(os.getenv('RESCUEPLATE_DB',str(ROOT/'rescueplate.sqlite3')))
if not db.exists():
    raise SystemExit('No existing demo database found. Start the app normally to create one.')
# SQLite online backup is consistent even with WAL; stop the app first for clarity.
backup=db.with_name(db.stem+'.backup-'+datetime.now().strftime('%Y%m%d-%H%M%S')+db.suffix)
conn=sqlite3.connect(db)
conn.row_factory=sqlite3.Row
with sqlite3.connect(backup) as dest: conn.backup(dest)
now=datetime.now(timezone.utc)
start=(now-timedelta(minutes=2)).isoformat(timespec='seconds')
end=(now+timedelta(minutes=118)).isoformat(timespec='seconds')
records=[('Chicken Kofta Meal',204,173),('Chicken Panne Meal',267,219),('Shish Tawook Meal',238,190),('Half Grilled Chicken Meal',296,251),('Chicken Hawawshi Sandwich',99,84),('Chicken Panne Sandwich',127,108),('Grilled Kofta Sandwich',139,111),('Béchamel Pasta Tajine',162,138)]
updated=0; skipped=0
try:
    conn.execute('BEGIN IMMEDIATE')
    for title,orig,new in records:
        meal=conn.execute("SELECT m.id FROM meals m JOIN cafeterias c ON c.id=m.cafeteria_id JOIN users u ON u.id=c.user_id WHERE m.title=? AND m.original_price=? AND u.email IN ('cafe@rescueplate.local','kitchen@rescueplate.local','fresh@rescueplate.local')",(title,orig)).fetchone()
        if not meal: skipped+=1;continue
        active=conn.execute("SELECT COUNT(*) FROM reservations WHERE meal_id=? AND status='reserved'",(meal['id'],)).fetchone()[0]
        if active:skipped+=1;continue
        conn.execute('UPDATE meals SET price=?,pickup_start=?,pickup_end=?,prepared_at=?,safe_until=?,active=1 WHERE id=?',(new,start,end,(now-timedelta(minutes=10)).isoformat(timespec='seconds'),end,meal['id']))
        updated+=1
    conn.commit()
finally:conn.close()
print(f'Demo meals refreshed: {updated}; skipped (active bookings/missing): {skipped}.')
print(f'Database backup: {backup}')
print('Next: restart the website. Bookings close ~58 minutes after refresh; pickup ends ~118 minutes after refresh.')
