self.addEventListener('push', event => {
  const data = event.data.json();
  const title = data.title || '天赋管家';
  const options = {
    body: data.body || '',
    icon: '/icon.png',
    badge: '/icon.png'
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(
    clients.openWindow('/')
  );
});