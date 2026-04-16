from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0005_remove_comboitem_combo_alter_documentline_combo_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'sales_payment' AND column_name = 'currency'
                ) THEN
                    ALTER TABLE sales_payment ALTER COLUMN currency SET DEFAULT 'USD';
                    UPDATE sales_payment SET currency = 'USD' WHERE currency IS NULL;
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'sales_payment' AND column_name = 'rate_to_usd'
                ) THEN
                    ALTER TABLE sales_payment ALTER COLUMN rate_to_usd SET DEFAULT 1.0;
                    UPDATE sales_payment SET rate_to_usd = 1.0 WHERE rate_to_usd IS NULL;
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'sales_payment' AND column_name = 'meta'
                ) THEN
                    ALTER TABLE sales_payment ALTER COLUMN meta SET DEFAULT '{}'::jsonb;
                    UPDATE sales_payment SET meta = '{}'::jsonb WHERE meta IS NULL;
                END IF;
            END
            $$;
            """,
            reverse_sql="""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'sales_payment' AND column_name = 'currency'
                ) THEN
                    ALTER TABLE sales_payment ALTER COLUMN currency DROP DEFAULT;
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'sales_payment' AND column_name = 'rate_to_usd'
                ) THEN
                    ALTER TABLE sales_payment ALTER COLUMN rate_to_usd DROP DEFAULT;
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'sales_payment' AND column_name = 'meta'
                ) THEN
                    ALTER TABLE sales_payment ALTER COLUMN meta DROP DEFAULT;
                END IF;
            END
            $$;
            """,
        ),
    ]
