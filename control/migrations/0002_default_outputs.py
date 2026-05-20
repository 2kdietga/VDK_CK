from django.db import migrations


def create_default_outputs(apps, schema_editor):
    OutputTarget = apps.get_model('control', 'OutputTarget')
    defaults = [
        {
            'key': 'led',
            'name': 'LED',
            'kind': 'light',
            'current_state': {'target': 'led', 'state': False, 'value': 0},
        },
        {
            'key': 'fan',
            'name': 'Fan',
            'kind': 'fan',
            'current_state': {'target': 'fan', 'state': False, 'value': 0},
        },
    ]

    for item in defaults:
        OutputTarget.objects.get_or_create(
            key=item['key'],
            defaults={
                'name': item['name'],
                'kind': item['kind'],
                'current_state': item['current_state'],
                'is_enabled': True,
            },
        )


def remove_default_outputs(apps, schema_editor):
    OutputTarget = apps.get_model('control', 'OutputTarget')
    OutputTarget.objects.filter(key__in=['led', 'fan']).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('control', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_default_outputs, remove_default_outputs),
    ]
