#!/usr/bin/env python3
"""Daily retention cleanup. Removes expired correction contact data and admin sessions."""
from server import connect, init_db, now

def main():
    init_db()
    with connect() as db:
        contacts=db.execute("UPDATE correction_report SET contact=NULL WHERE contact IS NOT NULL AND retain_until<=?",(now(),)).rowcount
        sessions=db.execute('DELETE FROM admin_session WHERE expires_at<=?',(now(),)).rowcount
    print(f'Expired contact details removed: {contacts}; expired admin sessions removed: {sessions}.')
if __name__=='__main__': main()
