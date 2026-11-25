

الاستيراد:
أنشئ قاعدة PostgreSQL جديدة فارغة تمامًا.

createdb -U postgres amira_training_lab
psql -U postgres -d amira_training_lab -v ON_ERROR_STOP=1 -f amira_training_original_invoices_dirty_custom.sql

تحقق:
psql -U postgres -d amira_training_lab -c "SELECT COUNT(*) FROM public.products;"
psql -U postgres -d amira_training_lab -c "SELECT COUNT(*) FROM public.invoices;"
psql -U postgres -d amira_training_lab -c "SELECT COUNT(*) FROM public.sales_people;"

