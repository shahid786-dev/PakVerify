"""
criminals/management/commands/seed_criminal_records.py
=======================================================
Seeds the database with realistic dummy criminal records for demo/testing.

Usage:
    python manage.py seed_criminal_records
    python manage.py seed_criminal_records --flush   # Delete all first
"""
from django.core.management.base import BaseCommand
from datetime import date


DUMMY_DATA = [
    {
        'cnic': '35202-1234567-1',
        'name': 'Ghulam Sarwar',
        'fir_number': 'FIR-456/2022',
        'crime_type': 'Armed Robbery',
        'police_station': 'Sheikhupura Saddar PS',
        'crime_severity': 'SERIOUS',
        'is_wanted': False,
        'is_blacklisted': True,
        'arrest_date': date(2022, 8, 10),
        'release_date': None,
        'status': 'CRIMINAL_MATCH',
        'previous_records': '["FIR-12/2019 - Theft", "FIR-87/2020 - Trespassing"]',
    },
    {
        'cnic': '42301-9876543-2',
        'name': 'Shahbaz Hussain',
        'fir_number': 'FIR-891/2021',
        'crime_type': 'Drug Trafficking',
        'police_station': 'Gulshan PS, Karachi',
        'crime_severity': 'SERIOUS',
        'is_wanted': True,
        'is_blacklisted': True,
        'arrest_date': date(2021, 6, 15),
        'release_date': None,
        'status': 'CRIMINAL_MATCH',
        'previous_records': '["FIR-334/2018 - Possession of narcotics"]',
    },
    {
        'cnic': '34101-5551234-3',
        'name': 'Pervez Khan',
        'fir_number': 'FIR-223/2023',
        'crime_type': 'Vehicle Theft',
        'police_station': 'Hayatabad PS, Peshawar',
        'crime_severity': 'MINOR',
        'is_wanted': False,
        'is_blacklisted': False,
        'arrest_date': date(2023, 2, 20),
        'release_date': date(2023, 11, 15),
        'status': 'SUSPECTED',
        'previous_records': '[]',
    },
    {
        'cnic': '38403-7654321-4',
        'name': 'Rashid Mehmood',
        'fir_number': 'FIR-102/2024',
        'crime_type': 'Fraud / Cheque Bounce',
        'police_station': 'Rawalpindi Cantt PS',
        'crime_severity': 'MINOR',
        'is_wanted': False,
        'is_blacklisted': False,
        'arrest_date': date(2024, 1, 8),
        'release_date': date(2024, 3, 10),
        'status': 'SUSPECTED',
        'previous_records': '["FIR-556/2022 - Breach of contract"]',
    },
    {
        'cnic': '35401-1122334-5',
        'name': 'Zulfiqar Ali',
        'fir_number': 'FIR-778/2020',
        'crime_type': 'Land Grabbing / Extortion',
        'police_station': 'Multan Cantt PS',
        'crime_severity': 'SERIOUS',
        'is_wanted': False,
        'is_blacklisted': True,
        'arrest_date': date(2020, 9, 25),
        'release_date': None,
        'status': 'CRIMINAL_MATCH',
        'previous_records': '["FIR-45/2016 - Assault", "FIR-89/2018 - Extortion"]',
    },
    {
        'cnic': '36502-4433221-6',
        'name': 'Imtiaz Butt',
        'fir_number': 'FIR-55/2023',
        'crime_type': 'Cyber Crime / Bank Fraud',
        'police_station': 'FIA Cybercrime Wing, Lahore',
        'crime_severity': 'SERIOUS',
        'is_wanted': True,
        'is_blacklisted': True,
        'arrest_date': None,
        'release_date': None,
        'status': 'CRIMINAL_MATCH',
        'previous_records': '["FIR-190/2020 - Online scam", "FIR-203/2021 - Identity theft"]',
    },
    {
        'cnic': '35202-6677889-7',
        'name': 'Naeem Abbas',
        'fir_number': 'FIR-331/2022',
        'crime_type': 'Kidnapping for Ransom',
        'police_station': 'Model Town PS, Lahore',
        'crime_severity': 'SERIOUS',
        'is_wanted': True,
        'is_blacklisted': True,
        'arrest_date': None,
        'release_date': None,
        'status': 'CRIMINAL_MATCH',
        'previous_records': '["FIR-78/2019 - Abduction"]',
    },
    {
        'cnic': '31303-2244668-8',
        'name': 'Tariq Mahmood',
        'fir_number': 'FIR-67/2021',
        'crime_type': 'Domestic Violence',
        'police_station': 'Quetta City PS',
        'crime_severity': 'MINOR',
        'is_wanted': False,
        'is_blacklisted': False,
        'arrest_date': date(2021, 3, 10),
        'release_date': date(2021, 6, 30),
        'status': 'SUSPECTED',
        'previous_records': '[]',
    },
    {
        'cnic': '42201-3344556-9',
        'name': 'Waseem Akram Baloch',
        'fir_number': 'FIR-199/2023',
        'crime_type': 'Human Trafficking',
        'police_station': 'Lyari PS, Karachi',
        'crime_severity': 'SERIOUS',
        'is_wanted': True,
        'is_blacklisted': True,
        'arrest_date': date(2023, 7, 15),
        'release_date': None,
        'status': 'CRIMINAL_MATCH',
        'previous_records': '["FIR-110/2020 - Smuggling"]',
    },
    {
        'cnic': '35201-9988776-0',
        'name': 'Khalid Pervez',
        'fir_number': 'FIR-412/2024',
        'crime_type': 'Tax Evasion / Money Laundering',
        'police_station': 'FBR Intelligence Unit, Lahore',
        'crime_severity': 'SERIOUS',
        'is_wanted': False,
        'is_blacklisted': True,
        'arrest_date': date(2024, 4, 20),
        'release_date': None,
        'status': 'CRIMINAL_MATCH',
        'previous_records': '["FIR-89/2022 - Hawala transactions"]',
    },
    {
        'cnic': '45208-3456710-3',
        'name': 'Unverified Individual',
        'fir_number': 'N/A',
        'crime_type': 'Manual Review Required',
        'police_station': 'Pending Verification',
        'crime_severity': 'MINOR',
        'is_wanted': False,
        'is_blacklisted': False,
        'arrest_date': None,
        'release_date': None,
        'status': 'SUSPECTED',
        'previous_records': '[]',
    },
    {
        'cnic': '45208-7894506-7',
        'name': 'Unverified Individual',
        'fir_number': 'N/A',
        'crime_type': 'Manual Review Required',
        'police_station': 'Pending Verification',
        'crime_severity': 'MINOR',
        'is_wanted': False,
        'is_blacklisted': False,
        'arrest_date': None,
        'release_date': None,
        'status': 'SUSPECTED',
        'previous_records': '[]',
    },
]


class Command(BaseCommand):
    help = 'Seed the criminal records database with realistic dummy data for demo purposes.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--flush',
            action='store_true',
            help='Delete all existing criminal records before seeding.',
        )

    def handle(self, *args, **options):
        from criminals.models import CriminalRecord

        if options['flush']:
            count, _ = CriminalRecord.objects.all().delete()
            self.stdout.write(self.style.WARNING(f'Deleted {count} existing criminal records.'))

        created = 0
        updated = 0

        for data in DUMMY_DATA:
            obj, was_created = CriminalRecord.objects.update_or_create(
                cnic=data['cnic'],
                defaults=data,
            )
            if was_created:
                created += 1
                self.stdout.write(f'  [CREATED] {obj.name} ({obj.cnic})')
            else:
                updated += 1
                self.stdout.write(f'  [UPDATED] {obj.name} ({obj.cnic})')

        self.stdout.write(self.style.SUCCESS(
            f'\nSeeding complete!\n'
            f'   Created  : {created}\n'
            f'   Updated  : {updated}\n'
            f'   Total DB : {CriminalRecord.objects.count()} records\n'
        ))
