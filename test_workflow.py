"""End-to-end API regression checks; run: python test_workflow.py."""
import os,tempfile
from pathlib import Path
fd,path=tempfile.mkstemp(suffix='.sqlite3');os.close(fd);os.unlink(path)
os.environ['RESCUEPLATE_DB']=path
os.environ['DEMO_MODE']='1'
from fastapi.testclient import TestClient
from app import app, db, iso, now, validate_slot
from datetime import timedelta
from fastapi import HTTPException
try:
    with TestClient(app) as guest, TestClient(app) as student, TestClient(app) as partner, TestClient(app) as admin:
        for client in (guest,student,partner,admin):client.headers.update({'X-Requested-With':'RescuePlate'})
        assert guest.get('/').status_code==200
        assert guest.get('/api/meals').status_code==200
        original=guest.get('/api/meals').json()['meals']
        assert len(original)==8,len(original)
        assert all(0.14 <= 1-x['price']/x['original_price'] <= 0.21 for x in original)
        assert all((__import__('datetime').datetime.fromisoformat(x['pickup_end'])-__import__('datetime').datetime.fromisoformat(x['pickup_start'])).total_seconds()<=120*60 for x in original)
        assert all(59*60 <= (__import__('datetime').datetime.fromisoformat(x['booking_end'])-__import__('datetime').datetime.fromisoformat(x['pickup_start'])).total_seconds() <= 60*60 for x in original)
        assert all((__import__('datetime').datetime.fromisoformat(x['pickup_end'])-__import__('datetime').datetime.fromisoformat(x['booking_end'])).total_seconds()>=60*60 for x in original)
        try:
            validate_slot(iso(now()),iso(now()+timedelta(minutes=125)))
            raise AssertionError('Too-long pickup slot was accepted')
        except HTTPException as ex: assert ex.status_code==400
        assert all(x['ingredients'] and x['allergens'] and x['storage_instructions'] for x in original)
        m=original[0]
        assert student.post('/api/login',json={'email':'student@rescueplate.local','password':'Demo@12345'}).status_code==200
        assert partner.post('/api/login',json={'email':'cafe@rescueplate.local','password':'Demo@12345'}).status_code==200
        assert admin.post('/api/login',json={'email':'admin@rescueplate.local','password':'DemoAdmin@123!'}).status_code==200
        for client in (student,partner,admin):client.headers.update({'X-CSRF-Token':client.get('/api/csrf').json()['csrf_token']})
        assert student.get('/api/admin/overview').status_code==403
        assert student.post('/api/meals',json={}).status_code==422
        r=student.post(f'/api/meals/{m["id"]}/reserve',json={})
        assert r.status_code==200,r.text
        data=r.json();code=data['code'];rid=data['id']
        assert student.post(f'/api/meals/{m["id"]}/reserve',json={}).status_code==409
        assert student.post(f'/api/meals/{m["id"]}/reserve',json={},headers={'X-CSRF-Token':'invalid'}).status_code==403
        # All mutating operations reject a forged Origin header.
        assert student.post(f'/api/meals/{m["id"]}/reserve',json={},headers={'Origin':'https://attacker.invalid'}).status_code==403
        assert student.get(f'/api/reservations/{rid}/qr').headers['content-type']=='image/png'
        assert guest.get(f'/api/reservations/{rid}/qr').status_code==401
        after=guest.get('/api/meals').json()['meals']
        assert next(x for x in after if x['id']==m['id'])['quantity']==m['quantity']-1
        if m['cafeteria_name'].startswith('Campus'):
            claimed=partner.post('/api/cafeteria/collect',json={'code':code})
            assert claimed.status_code==200,claimed.text
            assert partner.post('/api/cafeteria/collect',json={'code':code}).status_code==409
            assert student.post(f'/api/reservations/{rid}/cancel',json={}).status_code==409
        # New bookings close at the earlier of 60 minutes after start or 10 minutes before pickup deadline.
        closed=next(x for x in original if x['id']!=m['id'])
        with db() as c:
            c.execute('UPDATE meals SET pickup_end=? WHERE id=?',(iso(now()+timedelta(minutes=8)),closed['id']))
        assert student.post(f'/api/meals/{closed["id"]}/reserve',json={}).status_code==409
        # Booking closes at one hour even while pickup remains open for another hour.
        separate=next(x for x in original if x['id'] not in (m['id'],closed['id']))
        with db() as c:
            c.execute('UPDATE meals SET pickup_start=?,pickup_end=? WHERE id=?',(iso(now()-timedelta(minutes=61)),iso(now()+timedelta(minutes=59)),separate['id']))
        assert student.post(f'/api/meals/{separate["id"]}/reserve',json={}).status_code==409
        overview=admin.get('/api/admin/overview').json();assert overview['stats']['students']==1
        assert any(a['event']=='security_rejected' for a in overview['audit'])
        newcomer=guest.post('/api/register',json={'name':'New Partner','email':'newpartner@example.com','password':'SomePassword12!','role':'cafeteria','cafeteria_name':'New cafe','location':'Test location'})
        assert newcomer.status_code==200,newcomer.text
        guest.headers.update({'X-CSRF-Token':guest.get('/api/csrf').json()['csrf_token']})
        new_id=next(x['id'] for x in admin.get('/api/admin/overview').json()['cafeterias'] if x['name']=='New cafe')
        assert admin.patch(f'/api/admin/cafeterias/{new_id}/approval',json={'approved':True}).status_code==200
        assert guest.post('/api/login',json={'email':'student@rescueplate.local','password':'Demo@12345'},headers={'X-Requested-With':'bad'}).status_code==403
        # Reject booking before a listing opens, even with inventory remaining.
        upcoming=next(x for x in guest.get('/api/meals').json()['meals'] if x['id'] not in (m['id'],closed['id'],separate['id']))
        with db() as c:
            c.execute('UPDATE meals SET pickup_start=?,pickup_end=? WHERE id=?',(iso(now()+timedelta(minutes=20)),iso(now()+timedelta(minutes=140)),upcoming['id']))
        assert student.post(f'/api/meals/{upcoming["id"]}/reserve',json={}).status_code==409
        # Test food safety rules, meal publishing and available-stock edits.
        from datetime import datetime
        payload={'title':'Test Salad','title_ar':'سلطة للتجربة','description':'Test item', 'description_ar':'للاختبار فقط',
                 'category':'vegetarian','quantity':3,'price':80,'original_price':100,'image_emoji':'🥗',
                 'pickup_start':iso(now()+timedelta(minutes=2)),'pickup_end':iso(now()+timedelta(minutes=90)),
                 'prepared_at':iso(now()-timedelta(minutes=5)),'safe_until':iso(now()+timedelta(minutes=95)),
                 'ingredients':'Vegetables','allergens':'None (unverified)','storage_instructions':'Keep refrigerated; confirm food safety'}
        created=partner.post('/api/meals',json=payload)
        assert created.status_code==200,created.text
        created_id=created.json()['id']
        assert partner.patch(f'/api/meals/{created_id}/quantity',json={'quantity':8}).status_code==200
        assert student.patch(f'/api/meals/{created_id}/quantity',json={'quantity':300}).status_code==403
        invalid=dict(payload,safe_until=iso(now()+timedelta(minutes=20)))
        assert partner.post('/api/meals',json=invalid).status_code==400
        assert partner.patch(f'/api/meals/{created_id}/toggle',json={}).status_code==200
        assert partner.patch(f'/api/meals/{created_id}/quantity',json={'quantity':5}).status_code==409
        assert partner.patch(f'/api/meals/{created_id}/toggle',json={}).status_code==200
        # Unauthenticated and forged requests cannot create a meal.
        assert guest.post('/api/logout',json={}).status_code==200
        assert guest.post('/api/meals',json=payload).status_code==401
        # Non-collected reservation cancellation restores inventory exactly once.
        other=next(x for x in guest.get('/api/meals').json()['meals'] if x['id'] not in (m['id'],closed['id'],separate['id'],upcoming['id'],created_id))
        before_stock=other['quantity']
        reservation=student.post(f'/api/meals/{other["id"]}/reserve',json={})
        assert reservation.status_code==200
        other_id=reservation.json()['id']
        assert student.post(f'/api/reservations/{other_id}/cancel',json={}).status_code==200
        assert student.post(f'/api/reservations/{other_id}/cancel',json={}).status_code==409
        after_stock=next(x for x in guest.get('/api/meals').json()['meals'] if x['id']==other['id'])['quantity']
        assert after_stock==before_stock
        print('PASS: guest, demo meals, all roles, permissions, reservation stock, QR, collection, replay block, partner registration & approval')
finally:
    import glob
    for f in glob.glob(path+'*'):
        try:os.unlink(f)
        except:pass
