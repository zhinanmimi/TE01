// 确保所有时间处理使用北京时间
function getCurrentTime() {
  return moment().tz('Asia/Shanghai').format('YYYY-MM-DD HH:mm:ss');
}

function formatScheduleTime(timestamp) {
  return moment(timestamp).tz('Asia/Shanghai').format('HH:mm');
} 