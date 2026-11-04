-- Demo catalogue: 2 specialties and 2 doctors with fixed ids. The app runs this file only on the
-- first start (when there are no doctors yet), in the same transaction as the slot seed.
-- Idempotent anyway, so running it by hand against a filled database changes nothing.

INSERT INTO specialties (id, name) VALUES
    (1, 'Dermatology'),
    (2, 'General Practice')
ON CONFLICT DO NOTHING;

INSERT INTO doctors (id, name, specialty_id) VALUES
    (1, 'Dr. Salma Nasser', 1),
    (2, 'Dr. Khalid Rahman', 2)
ON CONFLICT DO NOTHING;
