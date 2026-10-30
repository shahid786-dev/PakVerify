from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('blockchain', '0002_alter_blockchainblock_action_type_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='EthereumAnchor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('certificate_number', models.CharField(db_index=True, max_length=30)),
                ('certificate_hash', models.CharField(db_index=True, max_length=64)),
                ('ledger_block_index', models.IntegerField()),
                ('ledger_block_hash', models.CharField(max_length=64)),
                ('chain_id', models.IntegerField()),
                ('contract_address', models.CharField(max_length=42)),
                ('tx_hash', models.CharField(blank=True, max_length=66)),
                ('status', models.CharField(choices=[('SUBMITTED', 'Submitted'), ('FAILED', 'Failed')], max_length=12)),
                ('error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
