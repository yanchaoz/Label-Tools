'use strict';
// UI strings are separate from annotation geometry and exported target semantics.
const messages = {
  en: {
    loading: 'Loading', export: 'Export reviewed', images: 'Images', all: 'All images',
    draft: 'Draft', complete: 'Complete', negative: 'Confirmed empty', instances: 'Instances',
    partial: 'Truncated / incomplete', opacity: 'Fill opacity', brightness: 'Brightness',
    contrast: 'Contrast', resetDisplay: 'Reset display', notes: 'Review notes',
    markComplete: 'Complete image', markNegative: 'Confirm no targets', reopen: 'Return to draft',
    local: 'Local save / read-only originals', draw: 'Draw polygon', edit: 'Edit vertices',
    pan: 'Pan image', undo: 'Undo', redo: 'Redo', close: 'Close contour', cancelPath: 'Cancel contour',
    fit: 'Fit image', zoomOut: 'Zoom out', zoomIn: 'Zoom in', overlay: 'Toggle contours',
    save: 'Save locally', backup: 'Download draft JSON', reload: 'Reload saved revision',
    previous: 'Previous image', next: 'Next image', deleteVertex: 'Delete selected vertex',
    deleteInstance: 'Delete selected instance', saved: 'Saved locally', unsaved: 'Unsaved changes',
    blank: 'Unannotated', openPath: 'Open contour', saving: 'Saving', error: 'Action failed',
    noInstances: 'No instances', instance: 'Instance', points: 'points', confirm: 'Confirm',
    cancel: 'Cancel', confirmTitle: 'Review confirmation', closeFirst: 'Close or cancel the current contour first.',
    leavePath: 'Discard the open contour and switch images?',
    confirmComplete: 'Have all targets in this image been traced and incomplete targets marked?',
    confirmNegative: 'Confirm there are no targets anywhere in this image? It will be exported as a reviewed negative.',
    discard: 'Reload the saved revision and discard your current edits? Download a draft backup first if needed.',
    exported: 'Reviewed images exported:', recovery: 'Restore the unsaved draft stored in this browser?',
    conflictRecovery: 'An older unsaved draft exists. Download it before replacing it with the newer saved revision.',
    backupReady: 'Draft backup downloaded.', maxId: 'No further uint16 instance IDs available.',
    readError: 'Could not load the image. Your prior edits have not been discarded.',
  },
  zh: {
    loading: '正在读取', export: '导出已完成标注', images: '视野', all: '全部视野', draft: '待完成',
    complete: '已描边', negative: '已确认无目标', instances: '实例', partial: '边缘截断 / 不完整',
    opacity: '填充透明度', brightness: '亮度', contrast: '对比度', resetDisplay: '重置显示',
    notes: '标注备注', markComplete: '完成本视野', markNegative: '确认无目标', reopen: '返回草稿',
    local: '本机保存 / 原图只读', draw: '多边形描边', edit: '编辑顶点', pan: '移动视野',
    undo: '撤销', redo: '重做', close: '闭合当前轮廓', cancelPath: '取消当前轮廓', fit: '适应视野',
    zoomOut: '缩小', zoomIn: '放大', overlay: '显示或隐藏标注', save: '保存到本机',
    backup: '下载草稿 JSON', reload: '重新读取已保存版本', previous: '上一视野', next: '下一视野',
    deleteVertex: '删除选中顶点', deleteInstance: '删除选中实例', saved: '已保存到本机',
    unsaved: '未保存', blank: '空白标注', openPath: '轮廓未闭合', saving: '正在保存', error: '操作失败',
    noInstances: '暂无实例', instance: '实例', points: '点', confirm: '确认', cancel: '取消',
    confirmTitle: '标注确认', closeFirst: '请先闭合或取消当前轮廓。',
    leavePath: '当前轮廓尚未闭合，放弃这段轮廓并切换视野？',
    confirmComplete: '确认已描出本视野内所有目标，并标记不完整目标？',
    confirmNegative: '确认当前整张视野没有目标？此决定会将它作为人工确认的负样本。',
    discard: '重新读取已保存版本并放弃当前编辑？需要保留时请先下载草稿备份。',
    exported: '已导出视野数：', recovery: '恢复当前浏览器中尚未保存的草稿？',
    conflictRecovery: '发现较旧的未保存草稿。请先下载备份，再读取较新的已保存版本。',
    backupReady: '已下载草稿备份。', maxId: '实例数量已达 uint16 编号上限。',
    readError: '无法读取原图，之前的编辑未被丢弃。',
  },
};
let language = 'en';
const shortcuts = { draw: 'P', edit: 'V', pan: 'H / Space', undo: 'Ctrl+Z', redo: 'Ctrl+Y',
  close: 'Enter', cancelPath: 'Esc', fit: 'F', save: 'Ctrl+S' };
const t = key => messages[language][key] || messages.en[key] || key;
function localize(value) {
  language = Object.hasOwn(messages, value) ? value : 'en';
  document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
  document.getElementById('language').value = language;
  document.querySelectorAll('[data-text]').forEach(element => { element.textContent = t(element.dataset.text); });
  document.querySelectorAll('[data-tool]').forEach(element => {
    const key = element.dataset.tool;
    element.setAttribute('aria-label', t(key));
    element.title = t(key) + (shortcuts[key] ? ` (${shortcuts[key]})` : '');
  });
}
