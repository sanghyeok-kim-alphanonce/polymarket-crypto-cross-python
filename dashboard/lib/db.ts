import { Pool } from 'pg';

const pool = new Pool({
  host: process.env.DB_HOST || 'localhost',
  port: parseInt(process.env.DB_PORT || '5435'),
  database: process.env.DB_NAME || 'paper_trade',
  user: process.env.DB_USER || 'paper',
  password: process.env.DB_PASSWORD || 'papertrade',
});

export default pool;
