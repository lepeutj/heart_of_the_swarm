-- This database is owned by sales_owner. Heart's MCP server authenticates as a separate role
-- that has only CONNECT, schema USAGE, and table SELECT privileges.

REVOKE ALL ON DATABASE sales_demo FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE TABLE customers (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    region text NOT NULL
);

CREATE TABLE orders (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES customers(id),
    status text NOT NULL CHECK (status IN ('pending', 'paid', 'shipped')),
    total_amount numeric(12, 2) NOT NULL CHECK (total_amount >= 0),
    ordered_at timestamptz NOT NULL
);

CREATE TABLE order_items (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id integer NOT NULL REFERENCES orders(id),
    product_name text NOT NULL,
    quantity integer NOT NULL CHECK (quantity > 0),
    unit_price numeric(12, 2) NOT NULL CHECK (unit_price >= 0)
);

INSERT INTO customers (name, region) VALUES
    ('Atelier Martin', 'Île-de-France'),
    ('Studio Rivage', 'Bretagne'),
    ('Maison Verne', 'Auvergne-Rhône-Alpes'),
    ('Café des Arts', 'Occitanie');

INSERT INTO orders (customer_id, status, total_amount, ordered_at) VALUES
    (1, 'shipped', 1299.00, CURRENT_DATE - INTERVAL '2 days'),
    (2, 'paid', 478.50, CURRENT_DATE - INTERVAL '5 days'),
    (3, 'shipped', 825.00, CURRENT_DATE - INTERVAL '11 days'),
    (1, 'paid', 349.00, CURRENT_DATE - INTERVAL '18 days'),
    (4, 'pending', 199.50, CURRENT_DATE - INTERVAL '27 days'),
    (2, 'shipped', 1599.00, CURRENT_DATE - INTERVAL '45 days');

INSERT INTO order_items (order_id, product_name, quantity, unit_price) VALUES
    (1, 'AI Workstation', 1, 1299.00),
    (2, 'Vector Database License', 1, 299.00),
    (2, 'Consulting Hour', 3, 59.83),
    (3, 'Inference Accelerator', 1, 825.00),
    (4, 'Monitoring Package', 1, 349.00),
    (5, 'Consulting Hour', 3, 66.50),
    (6, 'AI Workstation', 1, 1299.00),
    (6, 'Monitoring Package', 1, 300.00);

\getenv reader_password SALES_DB_READER_PASSWORD
CREATE ROLE heart_sales_reader LOGIN PASSWORD :'reader_password';
ALTER ROLE heart_sales_reader SET default_transaction_read_only = on;
ALTER ROLE heart_sales_reader SET statement_timeout = '5s';

GRANT CONNECT ON DATABASE sales_demo TO heart_sales_reader;
GRANT USAGE ON SCHEMA public TO heart_sales_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO heart_sales_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE sales_owner IN SCHEMA public
    GRANT SELECT ON TABLES TO heart_sales_reader;
