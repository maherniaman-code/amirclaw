module.exports = {
  apps: [{
    name: 'amirclaw',
    script: 'dist/index.js',
    restart_delay: 5000,
    max_restarts: 10,
    watch: false,
    env: {
      NODE_ENV: 'production'
    },
    error_file: 'logs/error.log',
    out_file: 'logs/out.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss'
  }]
};
