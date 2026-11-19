"""Deterministic request safety regressions. No sockets are opened."""
from dataclasses import replace
from datetime import date
from unittest.mock import patch

import requests
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from tracker.models import CatalogueDiscoveryState, DiscoveredCatalogueRecord, Provider
from tracker.services.catalogue_discovery import CatalogueDiscoveryError, run_discovery
from tracker.services.catalogue_discovery_scheduler import process_due_discovery_schedules
from tracker.services.discovery_http import DiscoveryHTTP, RequestBudgetReached, public_addresses
from tracker.services.discovery_policy import DiscoveryPermission, DiscoveryPolicyError
from tracker.tests.discovery_fixtures import FakeClock

HOST = 'hydrahd.gd'
URL = f'https://{HOST}/movies/date/133'
PERMISSION = DiscoveryPermission('hydrahd', 'VERIFIED', HOST,
    (r'/robots\.txt', r'/movies/date/\d+/?'), 'offline policy fixture only',
    date(2026, 1, 1), date(2099, 1, 1), request_limit=5, page_limit=2,
    metadata_paths=(r'/metadata/\d+',))


def response(status=200, body='', **headers):
    r = requests.Response()
    r.status_code, r._content = status, body.encode()
    r.headers.update(headers)
    r.encoding = 'utf-8'
    return r


def listing(title='Offline Canary Movie'):
    identity = title.lower().replace(' ', '-')
    return f'''<div><a class="hthis" href="/movie/{identity}"><span>2020</span></a>
        <div class="title detz">{title}</div></div><a href="/movies/date/136">136</a>'''


class DiscoveryTransportTests(SimpleTestCase):
    def transport(self, **kwargs):
        return DiscoveryHTTP(PERMISSION, request_budget=kwargs.pop('request_budget', 5),
            clock=FakeClock(), resolver=lambda _: ['93.184.216.34'], **kwargs)

    def test_every_request_kind_shares_spacing_and_budget(self):
        client = self.transport()
        with patch.object(client, '_send', return_value=response()) as send:
            client.get(f'https://{HOST}/robots.txt', kind='robots')
            client.get(URL)
            client.get(f'https://{HOST}/metadata/133', kind='metadata')
            client.get(URL, retry=True)
        self.assertEqual(send.call_count, 4)
        self.assertEqual([e['started'] for e in client.events], [0, 2, 4, 6])
        self.assertEqual(client.counts['robots'], 1)
        self.assertEqual(client.counts['listing'], 2)
        self.assertEqual(client.counts['metadata'], 1)
        self.assertEqual(client.counts['retries'], 1)

    def test_request_exhaustion_and_failed_requests_consume_budget(self):
        client = self.transport(request_budget=1)
        with patch.object(client, '_send', side_effect=requests.Timeout('secret')) as send:
            with self.assertRaises(requests.Timeout): client.get(URL)
            with self.assertRaises(RequestBudgetReached): client.get(URL)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(client.attempted, 1)
        self.assertNotIn('secret', str(client.report()))

    def test_provider_limit_caps_operator_budget(self):
        client = self.transport(request_budget=500)
        with patch.object(client, '_send', return_value=response()) as send:
            for _ in range(5): client.get(URL)
            with self.assertRaises(RequestBudgetReached): client.get(URL)
        self.assertEqual(send.call_count, 5)
        self.assertEqual(client.limit, PERMISSION.request_limit)

    def test_redirect_accounting_spacing_and_sensitive_headers(self):
        client = self.transport()
        with patch.object(client, '_send', side_effect=[response(301, Location=URL+'/'), response()]) as send:
            client.get(URL, headers={'Authorization': 'secret', 'Cookie': 'secret',
                'Referer': 'secret', 'Accept': 'text/html'})
        self.assertEqual(client.attempted, 2)
        self.assertEqual(client.counts['redirects'], 1)
        self.assertEqual([e['started'] for e in client.events], [0, 2])
        for call in send.call_args_list:
            self.assertEqual(call.args[2], {'Accept': 'text/html'})
        self.assertNotIn('secret', str(client.report()))

    def test_cross_domain_redirect_is_rejected_before_destination_dns(self):
        client = self.transport()
        with patch.object(client, '_send', return_value=response(302, Location='https://hydrahd.ws/movies/date/133')) as send:
            with self.assertRaisesRegex(DiscoveryPolicyError, 'url_policy_rejected'): client.get(URL)
        self.assertEqual(send.call_count, 1)

    def test_private_network_redirects_and_unsafe_urls(self):
        for destination in ['https://127.0.0.1/x', 'https://169.254.169.254/x',
                'https://[::1]/x', 'https://10.0.0.1/x', 'http://hydrahd.gd/movies/date/133',
                'https://user:secret@hydrahd.gd/movies/date/133',
                'https://hydrahd.gd:444/movies/date/133', 'https://hydrahd.gd./movies/date/133']:
            with self.subTest(destination=destination):
                client = self.transport()
                with patch.object(client, '_send', return_value=response(302, Location=destination)) as send:
                    with self.assertRaises(DiscoveryPolicyError): client.get(URL)
                self.assertEqual(send.call_count, 1)

    def test_private_mixed_dns_answers_are_rejected_without_dispatch(self):
        client = self.transport()
        client.resolver = lambda _: ['93.184.216.34', '10.0.0.1']
        with patch.object(client, '_send') as send:
            with self.assertRaisesRegex(DiscoveryPolicyError, 'network_destination'): client.get(URL)
        send.assert_not_called()
        self.assertEqual(client.attempted, 0)

    def test_dns_resolver_rejects_private_ipv6_and_mapped_addresses(self):
        for address in ['127.0.0.1', '::1', '::ffff:127.0.0.1', '169.254.169.254', '100.64.0.1',
                '224.0.0.1', 'ff02::1', 'fec0::1', '0.0.0.0', '::']:
            with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', (address, 443))]):
                with self.assertRaises(DiscoveryPolicyError): public_addresses(HOST)

    def test_redirect_loop_and_finite_limit(self):
        client = self.transport()
        with patch.object(client, '_send', side_effect=[response(302, Location=URL+'/'), response(302, Location=URL)]) as send:
            with self.assertRaisesRegex(DiscoveryPolicyError, 'redirect_loop'): client.get(URL)
        self.assertEqual(send.call_count, 2)
        client = self.transport(max_redirects=0)
        with patch.object(client, '_send', return_value=response(302, Location=URL+'/')) as send:
            with self.assertRaisesRegex(DiscoveryPolicyError, 'redirect_limit'): client.get(URL)
        self.assertEqual(send.call_count, 1)

    def test_redirect_cannot_bypass_page_or_request_budget(self):
        client = self.transport()
        with patch.object(client, '_send', return_value=response(302, Location=URL.replace('133', '134'))) as send:
            with self.assertRaisesRegex(DiscoveryPolicyError, 'resource_changed'): client.get(URL)
        self.assertEqual(send.call_count, 1)
        client = self.transport(request_budget=1)
        with patch.object(client, '_send', return_value=response(302, Location=URL+'/')) as send:
            with self.assertRaises(RequestBudgetReached): client.get(URL)
        self.assertEqual(send.call_count, 1)

    def test_tls_connection_pins_validated_address_and_disables_automatic_retries(self):
        client = self.transport()
        with patch('urllib3.HTTPSConnectionPool') as pool:
            raw = pool.return_value.urlopen.return_value
            raw.status, raw.headers, raw.data = 200, {}, b'ok'
            client.get(URL, headers={'User-Agent': 'canary'})
        self.assertEqual(pool.call_args.args[0], '93.184.216.34')
        self.assertEqual(pool.call_args.kwargs['assert_hostname'], HOST)
        self.assertEqual(pool.call_args.kwargs['server_hostname'], HOST)
        self.assertEqual(pool.call_args.kwargs['cert_reqs'], 'CERT_REQUIRED')
        call = pool.return_value.urlopen.call_args
        self.assertFalse(call.kwargs['redirect'])
        self.assertFalse(call.kwargs['retries'])
        self.assertEqual(call.kwargs['headers']['Host'], HOST)

    def test_direct_session_paths_cannot_bypass_controls(self):
        client = self.transport(request_budget=1)
        session = requests.Session()
        client.install(session)
        with patch.object(client, '_send', return_value=response()) as send:
            session.request('GET', URL)
            with self.assertRaises(RequestBudgetReached): session.get(URL)
            with self.assertRaises(DiscoveryPolicyError): session.post(URL)
            with self.assertRaises(DiscoveryPolicyError): session.send(requests.Request('GET', URL).prepare())
        self.assertEqual(send.call_count, 1)


class DiscoveryControlIntegrationTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.get(slug='hydrahd')
        self.clock = FakeClock()

    def discover(self, **kwargs):
        return run_discovery('hydrahd', mode='historical', partition=kwargs.pop('partition', 'movies'),
            max_pages=kwargs.pop('max_pages', 1), delay=2, retry_limit=kwargs.pop('retry_limit', 0),
            clock=self.clock, **kwargs)

    def approved(self):
        return patch('tracker.services.catalogue_discovery.permission_for', return_value=PERMISSION)

    def test_one_page_global_ceiling_and_checkpoint_resume(self):
        CatalogueDiscoveryState.objects.create(provider=self.provider, status='PARTIAL',
            cursor={'modes': {'historical': {'parts': {'movies': 132, 'tv-shows': 77}}}})
        with self.approved(), patch('tracker.services.discovery_http.public_addresses', return_value=['93.184.216.34']), \
                patch.object(DiscoveryHTTP, '_send', side_effect=[response(body='User-agent: *\nAllow: /'), response(body=listing())]) as send:
            result = self.discover(request_budget=5, partition=None)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(result['pages_attempted'], 1)
        self.assertEqual(result['pages_by_partition'], {'movies': [133]})
        self.assertEqual(result['checkpoint']['parts'], {'movies': 133, 'tv-shows': 77})
        self.assertEqual(result['request_accounting']['events'][1]['started'], 2)

    def test_failed_page_consumes_page_budget_without_advancing_checkpoint(self):
        with self.approved(), patch('tracker.services.discovery_http.public_addresses', return_value=['93.184.216.34']), \
                patch.object(DiscoveryHTTP, '_send', side_effect=[response(body='User-agent: *\nAllow: /'), requests.Timeout()]):
            with self.assertRaises(CatalogueDiscoveryError) as caught:
                self.discover(request_budget=5)
        state = CatalogueDiscoveryState.objects.get(provider=self.provider)
        self.assertEqual(state.report['pages_attempted'], 1)
        self.assertEqual(state.report['requests_dispatched'], 2)
        self.assertNotIn('movies', state.cursor['modes']['historical'].get('parts', {}))
        self.assertEqual(caught.exception.report['requests_made'], 2)

    def test_retry_spacing_and_attempt_accounting(self):
        with self.approved(), patch('tracker.services.discovery_http.public_addresses', return_value=['93.184.216.34']), \
                patch.object(DiscoveryHTTP, '_send', side_effect=[response(body='User-agent: *\nAllow: /'), requests.Timeout(), response(body=listing())]):
            result = self.discover(request_budget=5, retry_limit=1)
        self.assertEqual(result['requests_made'], 3)
        self.assertEqual(result['request_accounting']['counts']['retries'], 1)
        times = [e['started'] for e in result['request_accounting']['events']]
        self.assertEqual(times, [0, 2, 4])

    def test_robots_crawl_delay_and_rate_are_shared(self):
        with self.approved(), patch('tracker.services.discovery_http.public_addresses', return_value=['93.184.216.34']), \
                patch.object(DiscoveryHTTP, '_send', side_effect=[response(body='User-agent: *\nAllow: /\nCrawl-delay: 5\nRequest-rate: 1/7'), response(body=listing())]):
            result = self.discover(request_budget=5)
        self.assertEqual(result['request_accounting']['events'][1]['started'], 7)

    def test_retry_after_persisted_cooldown_blocks_every_mode_before_dispatch(self):
        with self.approved(), patch('tracker.services.discovery_http.public_addresses', return_value=['93.184.216.34']), \
                patch.object(DiscoveryHTTP, '_send', side_effect=[response(body='User-agent: *\nAllow: /'), response(429, **{'Retry-After': '120'})]):
            with self.assertRaises(CatalogueDiscoveryError): self.discover(request_budget=5, retry_limit=1)
        state = CatalogueDiscoveryState.objects.get(provider=self.provider)
        self.assertGreater(timezone.datetime.fromisoformat(state.cursor['modes']['historical']['cooldown_until']), timezone.now())
        with self.approved(), patch.object(DiscoveryHTTP, '_send') as send:
            result = run_discovery('hydrahd', mode='incremental')
        self.assertEqual(result['status'], 'COOLDOWN')
        send.assert_not_called()

    def test_interrupt_preserves_checkpoint_and_resumes_same_page(self):
        with self.approved(), patch('tracker.services.discovery_http.public_addresses', return_value=['93.184.216.34']), \
                patch.object(DiscoveryHTTP, '_send', side_effect=[response(body='User-agent: *\nAllow: /'), response(body=listing()), KeyboardInterrupt()]):
            first = self.discover(max_pages=2, request_budget=5)
        self.assertEqual(first['checkpoint']['parts']['movies'], 1)
        with self.approved(), patch('tracker.services.discovery_http.public_addresses', return_value=['93.184.216.34']), \
                patch.object(DiscoveryHTTP, '_send', side_effect=[response(body='User-agent: *\nAllow: /'), response(body=listing('Different Film'))]) as send:
            second = self.discover(request_budget=5)
        self.assertIn('/movies/date/2', send.call_args.args[0])
        self.assertEqual(second['checkpoint']['parts']['movies'], 2)

    def test_unverified_denied_expired_disabled_or_failed_preflight_never_dispatches(self):
        for permission in [replace(PERMISSION, status='UNVERIFIED'), replace(PERMISSION, status='DENIED'),
                replace(PERMISSION, expires_on=date(2020, 1, 1))]:
            with patch('tracker.services.catalogue_discovery.permission_for', return_value=permission), \
                    patch.object(DiscoveryHTTP, '_send') as send, patch('socket.getaddrinfo') as dns:
                result = self.discover()
            self.assertEqual(result['status'], 'BLOCKED')
            send.assert_not_called()
            dns.assert_not_called()
            self.assertFalse(CatalogueDiscoveryState.objects.filter(provider=self.provider).exists())
        with patch.object(DiscoveryHTTP, '_send') as send:
            self.assertEqual(self.discover()['last_error'], 'source_permission_unverified')
        send.assert_not_called()

    def test_disabled_schedules_cannot_dispatch(self):
        with patch.object(DiscoveryHTTP, '_send') as send:
            self.assertEqual(process_due_discovery_schedules(config={'enabled': False})['status'], 'DISABLED')
        send.assert_not_called()

    def test_request_exhaustion_after_robots_never_fetches_listing(self):
        with self.approved(), patch('tracker.services.discovery_http.public_addresses', return_value=['93.184.216.34']), \
                patch.object(DiscoveryHTTP, '_send', return_value=response(body='User-agent: *\nAllow: /')) as send:
            result = self.discover(request_budget=1)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result['pages_scanned'], 0)
        self.assertEqual(result['requests_made'], 1)
        self.assertFalse(DiscoveredCatalogueRecord.objects.exists())

    def test_provider_page_budget_caps_requested_window(self):
        with self.approved(), patch('tracker.services.discovery_http.public_addresses', return_value=['93.184.216.34']), \
                patch.object(DiscoveryHTTP, '_send', side_effect=[response(body='User-agent: *\nAllow: /'),
                    response(body=listing('First Movie')), response(body=listing('Second Movie'))]) as send:
            result = self.discover(max_pages=250, request_budget=5)
        self.assertEqual(result['pages_scanned'], 2)
        self.assertEqual(result['page_limit'], 2)
        self.assertEqual(send.call_count, 3)
        self.assertEqual(result['checkpoint']['parts']['movies'], 2)

    def test_invalid_page_limit_does_not_dispatch_or_write_state(self):
        with self.approved(), patch.object(DiscoveryHTTP, '_send') as send:
            for limit in (0, -1, 251, True):
                with self.assertRaises(ValueError): self.discover(max_pages=limit)
        send.assert_not_called()
        self.assertFalse(CatalogueDiscoveryState.objects.exists())

    def test_disabled_provider_and_operator_denial_cannot_dispatch(self):
        self.provider.is_enabled = False
        self.provider.save()
        with self.approved(), patch.object(DiscoveryHTTP, '_send') as send:
            self.assertEqual(self.discover()['last_error'], 'source_permission_denied')
        send.assert_not_called()
        self.provider.is_enabled = True
        self.provider.config = {'discovery_permission': 'DENIED'}
        self.provider.save()
        with patch.object(DiscoveryHTTP, '_send') as send:
            self.assertEqual(self.discover()['last_error'], 'source_permission_denied')
        send.assert_not_called()

    def test_rate_limit_without_retry_after_persists_a_cooldown(self):
        with self.approved(), patch('tracker.services.discovery_http.public_addresses', return_value=['93.184.216.34']), \
                patch.object(DiscoveryHTTP, '_send', return_value=response(429)):
            with self.assertRaises(CatalogueDiscoveryError): self.discover()
        state = CatalogueDiscoveryState.objects.get(provider=self.provider)
        self.assertIn('cooldown_until', state.cursor['modes']['historical'])

    def test_policy_violation_is_not_swallowed_by_scraper_exception_handlers(self):
        with self.approved(), patch('tracker.services.discovery_http.public_addresses', return_value=['93.184.216.34']), \
                patch.object(DiscoveryHTTP, '_send', return_value=response(302, Location='https://hydrahd.ws/robots.txt')) as send:
            with self.assertRaises(CatalogueDiscoveryError) as caught:
                self.discover(request_budget=5, retry_limit=2)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(caught.exception.report['status'], 'BLOCKED')
        self.assertEqual(caught.exception.report['pages_attempted'], 1)
