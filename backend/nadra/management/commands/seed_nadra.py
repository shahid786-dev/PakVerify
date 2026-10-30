"""
Management command: seed_nadra
Populates the NADRA dummy database with realistic Pakistani citizen records.
Run: python manage.py seed_nadra
"""

import datetime
import os
import shutil
from django.core.management.base import BaseCommand
from django.conf import settings
from django.core.files import File
from nadra.models import NADRARecord


DUMMY_RECORDS = [
    dict(cnic='35202-1234567-1', full_name='Muhammad Ali Khan',    father_name='Ghulam Ali Khan',
         date_of_birth=datetime.date(1990, 3, 15), gender='M',
         address='House 12, Street 5, Gulberg III', district='Lahore', province='Punjab'),
    dict(cnic='35202-2345678-2', full_name='Fatima Noor',          father_name='Abdul Noor',
         date_of_birth=datetime.date(1995, 7, 22), gender='F',
         address='Plot 44, Block D, PECHS', district='Karachi', province='Sindh'),
    dict(cnic='35202-3456789-3', full_name='Ahmed Hassan Siddiqui',father_name='Hassan Siddiqui',
         date_of_birth=datetime.date(1988, 11, 30), gender='M',
         address='Flat 7, Askari Apartments', district='Rawalpindi', province='Punjab'),
    dict(cnic='35202-4567890-4', full_name='Ayesha Malik',         father_name='Tariq Malik',
         date_of_birth=datetime.date(1993, 5, 10), gender='F',
         address='Bungalow 3, Phase 6, DHA', district='Lahore', province='Punjab'),
    dict(cnic='35202-5678901-5', full_name='Usman Farooq',         father_name='Farooq Ahmed',
         date_of_birth=datetime.date(1985, 8, 5), gender='M',
         address='House 88, Model Town', district='Lahore', province='Punjab'),
    dict(cnic='35202-6789012-6', full_name='Sana Rehman',          father_name='Abdul Rehman',
         date_of_birth=datetime.date(1998, 2, 28), gender='F',
         address='Street 14, F-7', district='Islamabad', province='ICT'),
    dict(cnic='35202-7890123-7', full_name='Bilal Chaudhry',       father_name='Saeed Chaudhry',
         date_of_birth=datetime.date(1982, 12, 1), gender='M',
         address='Village Chak 44, Tehsil Sargodha', district='Sargodha', province='Punjab'),
    dict(cnic='35202-8901234-8', full_name='Mariam Sheikh',        father_name='Iqbal Sheikh',
         date_of_birth=datetime.date(1991, 4, 19), gender='F',
         address='Unit 201, Clifton Block 5', district='Karachi', province='Sindh'),
    dict(cnic='35202-9012345-9', full_name='Zain ul Abideen',      father_name='Abid Hussain',
         date_of_birth=datetime.date(1997, 9, 6), gender='M',
         address='House 33, Chaklala Scheme III', district='Rawalpindi', province='Punjab'),
    dict(cnic='35202-0123456-0', full_name='Hina Baig',            father_name='Zafar Baig',
         date_of_birth=datetime.date(1994, 6, 14), gender='F',
         address='Flat 12, Askari 14', district='Peshawar', province='KPK'),
    dict(cnic='42101-1111111-1', full_name='Imran Raza Mirza',     father_name='Raza Mirza',
         date_of_birth=datetime.date(1979, 1, 20), gender='M',
         address='Plot 17, Tariq Road', district='Karachi', province='Sindh'),
    dict(cnic='42101-2222222-2', full_name='Nadia Qureshi',        father_name='Asif Qureshi',
         date_of_birth=datetime.date(1996, 10, 3), gender='F',
         address='House 55, G-9/2', district='Islamabad', province='ICT'),
    dict(cnic='42101-3333333-3', full_name='Faisal Javed',         father_name='Javed Akhtar',
         date_of_birth=datetime.date(1987, 3, 25), gender='M',
         address='Chaman Housing Scheme, Block B', district='Quetta', province='Balochistan'),
    dict(cnic='42101-4444444-4', full_name='Saira Hameed',         father_name='Abdul Hameed',
         date_of_birth=datetime.date(1992, 7, 7), gender='F',
         address='Street 4, Hayatabad Phase 2', district='Peshawar', province='KPK'),
    dict(cnic='42101-5555555-5', full_name='Omer Saleem',          father_name='Muhammad Saleem',
         date_of_birth=datetime.date(1983, 11, 11), gender='M',
         address='House 9, Shah Faisal Colony', district='Karachi', province='Sindh'),
    dict(cnic='42101-6666666-6', full_name='Rabia Tariq',          father_name='Tariq Hussain',
         date_of_birth=datetime.date(1999, 1, 30), gender='F',
         address='Johar Town Block A', district='Lahore', province='Punjab'),
    dict(cnic='42101-7777777-7', full_name='Shahid Nawaz',         father_name='Nawaz Ahmed',
         date_of_birth=datetime.date(1975, 5, 5), gender='M',
         address='House 2, Satellite Town', district='Rawalpindi', province='Punjab'),
    dict(cnic='42101-8888888-8', full_name='Asma Iqbal',           father_name='Iqbal Ahmed',
         date_of_birth=datetime.date(1989, 8, 17), gender='F',
         address='Block 14, Federal B Area', district='Karachi', province='Sindh'),
    dict(cnic='42101-9999999-9', full_name='Tariq Mehmood',        father_name='Mehmood ul Haq',
         date_of_birth=datetime.date(1980, 2, 28), gender='M',
         address='Village Kahuta, Tehsil Kahuta', district='Rawalpindi', province='Punjab'),
    dict(cnic='42101-0000000-0', full_name='Zunera Waseem',        father_name='Waseem Ahmad',
         date_of_birth=datetime.date(2000, 12, 5), gender='F',
         address='House 67, I-8/3', district='Islamabad', province='ICT'),
    # ── 9 Identity Card Records (linked to real face images) ─────────────────
    dict(cnic='42301-1000001-1', full_name='Mudasir Ali',          father_name='Ali Muhammad',
         date_of_birth=datetime.date(1998, 4, 10), gender='M',
         address='House 5, Gulshan-e-Iqbal Block 3', district='Karachi', province='Sindh'),
    dict(cnic='42301-1000002-2', full_name='Mudasir Shah',         father_name='Shah Muhammad',
         date_of_birth=datetime.date(1997, 8, 22), gender='M',
         address='Plot 12, North Nazimabad Block F', district='Karachi', province='Sindh'),
    dict(cnic='42301-1000003-3', full_name='Hamid Khan',           father_name='Amir Khan',
         date_of_birth=datetime.date(1995, 3, 5), gender='M',
         address='Street 7, Hayatabad Phase 4', district='Peshawar', province='KPK'),
    dict(cnic='42301-1000004-4', full_name='Siraj ul Haq',         father_name='Haq Nawaz',
         date_of_birth=datetime.date(1993, 11, 15), gender='M',
         address='House 22, Satellite Town Block A', district='Rawalpindi', province='Punjab'),
    dict(cnic='42301-1000005-5', full_name='Karan Das',            father_name='Roshan Das',
         date_of_birth=datetime.date(1999, 6, 20), gender='M',
         address='Flat 3, Defence View Phase 2', district='Karachi', province='Sindh'),
    dict(cnic='42301-1000006-6', full_name='Haroon Rashid',        father_name='Abdul Rashid',
         date_of_birth=datetime.date(1996, 1, 28), gender='M',
         address='House 9, G-13/2', district='Islamabad', province='ICT'),
    dict(cnic='42301-1000007-7', full_name='Abdul Mutali',         father_name='Ghulam Mutali',
         date_of_birth=datetime.date(1990, 9, 3), gender='M',
         address='Village Sohawa, Tehsil Gujar Khan', district='Rawalpindi', province='Punjab'),
    dict(cnic='42301-1000008-8', full_name='Eman Fatima',          father_name='Fateh Muhammad',
         date_of_birth=datetime.date(2001, 2, 14), gender='F',
         address='House 33, Model Colony', district='Karachi', province='Sindh'),
    dict(cnic='42301-1000009-9', full_name='Farhan Iqbal',         father_name='Iqbal Ahmed',
         date_of_birth=datetime.date(1994, 7, 7), gender='M',
         address='Plot 8, Bahria Town Phase 5', district='Lahore', province='Punjab'),
]

     # Maps CNIC to the specific named image filename in data/id-card/images
CNIC_TO_IMAGE_MAP = {
    '42301-1000001-1': 'MudasirAli.png',
    '42301-1000002-2': 'MudasirShah.png',
    '42301-1000003-3': 'Hamid.png',
    '42301-1000004-4': 'Siraj.png',
    '42301-1000005-5': 'Karan.png',
    '42301-1000006-6': 'Haroon.png',
    '42301-1000007-7': 'AbdulMutali.png',
    '42301-1000008-8': 'Eman.png',
    '42301-1000009-9': 'Farhan.png',
}


class Command(BaseCommand):
    help = 'Seed the NADRA dummy database with citizen records (including 9 identity card persons)'

    def handle(self, *args, **options):
        created_count = 0
        updated_images_count = 0

        # Ensure media directory exists
        media_nadra_dir = os.path.join(settings.MEDIA_ROOT, 'nadra_faces')
        os.makedirs(media_nadra_dir, exist_ok=True)

        dataset_dir = str(settings.BASE_DIR.parent / 'data' / 'id-card' / 'images')
        # The image dataset is optional (it is not committed to the repo).
        dataset_images = [] if not os.path.isdir(dataset_dir) else sorted([f for f in os.listdir(dataset_dir) if f.endswith('.png') or f.endswith('.jpg')])

        for idx, data in enumerate(DUMMY_RECORDS):
            obj, created = NADRARecord.objects.get_or_create(
                cnic=data['cnic'],
                defaults=data,
            )
            if created:
                created_count += 1

            # Try to link a face image if not already set
            if not obj.face_image:
                # 1. Check the CNIC_TO_IMAGE_MAP for named identity card images
                named_img = CNIC_TO_IMAGE_MAP.get(data['cnic'])
                if named_img:
                    img_path = os.path.join(dataset_dir, named_img)
                    if os.path.exists(img_path):
                        with open(img_path, 'rb') as f:
                            obj.face_image.save(named_img, File(f), save=True)
                        updated_images_count += 1
                        self.stdout.write(f'  Linked {named_img} -> {data["cnic"]} ({data["full_name"]})')
                        continue

                # 2. Fall back to positional dataset image
                numeric_idx = idx - len(CNIC_TO_IMAGE_MAP)  # offset for records without named images
                if 0 <= numeric_idx < len(dataset_images):
                    img_name = dataset_images[numeric_idx]
                    img_path = os.path.join(dataset_dir, img_name)
                    if os.path.exists(img_path):
                        with open(img_path, 'rb') as f:
                            obj.face_image.save(img_name, File(f), save=True)
                        updated_images_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'[SUCCESS] NADRA seed complete: {created_count} new records created '
                f'({len(DUMMY_RECORDS) - created_count} already existed). '
                f'{updated_images_count} images assigned.'
            )
        )
